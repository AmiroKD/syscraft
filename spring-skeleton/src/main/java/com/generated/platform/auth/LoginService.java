package com.generated.platform.auth;

import com.baomidou.mybatisplus.core.toolkit.Wrappers;
import com.generated.platform.admin.entity.LoginAttemptEntity;
import com.generated.platform.admin.entity.RoleEntity;
import com.generated.platform.admin.entity.UserEntity;
import com.generated.platform.admin.mapper.LoginAttemptMapper;
import com.generated.platform.admin.mapper.RoleMapper;
import com.generated.platform.admin.mapper.UserMapper;
import com.generated.platform.auth.dto.LoginRequest;
import com.generated.platform.auth.dto.LoginResult;
import com.generated.platform.auth.dto.MeResult;
import com.generated.platform.auth.dto.PasswordRequest;
import com.generated.platform.auth.dto.UserView;
import com.generated.platform.common.BizException;
import com.generated.platform.security.PermVersionService;
import com.generated.platform.security.SessionService;
import com.generated.platform.common.ErrorCode;
import com.github.benmanes.caffeine.cache.Cache;
import com.github.benmanes.caffeine.cache.Caffeine;
import cn.dev33.satoken.secure.BCrypt;
import jakarta.servlet.http.HttpServletRequest;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;
import org.springframework.web.context.request.RequestContextHolder;
import org.springframework.web.context.request.ServletRequestAttributes;

import java.time.Duration;
import java.time.LocalDateTime;

/**
 * 登录、当前用户、改密。
 *
 * <p>口令校验用 Sa-Token 自带的 BCrypt（jBCrypt 实现），不额外引入 spring-security-crypto，
 * 依赖清单保持在七个以内。</p>
 *
 * <p><b>锁定状态的落库（底座扩展 v0.1，接口清单 9.5 的升级）。</b>旧版用内存计数，
 * 注释里自己写着「重启可绕过」——现在锁定与计数都在 adm_user 行上
 * （failed_attempts / locked_until），重启不失忆；每次尝试另记
 * adm_login_attempt 一行，爆破来源（IP / UA）可回溯。唯一留在内存的是
 * <b>不存在的用户名</b>的失败计数：行不存在，锁没地方挂，也不能为了它去无限灌审计表。</p>
 */
@Service
public class LoginService {

    private static final Logger log = LoggerFactory.getLogger(LoginService.class);

    /** 连续失败 5 次锁 10 分钟；计数与截止时刻都在账号行上，成功登录清零 */
    private static final int MAX_FAILURES = 5;
    private static final Duration LOCK_DURATION = Duration.ofMinutes(10);

    private final UserMapper userMapper;
    private final RoleMapper roleMapper;
    private final TokenService tokenService;
    private final PermVersionService permVersionService;
    private final LoginAttemptMapper loginAttemptMapper;
    private final SessionService sessionService;

    /** key = username。只兜不存在的用户名：同一台机器上的多个账号互不影响 */
    private final Cache<String, Integer> unknownFailures = Caffeine.newBuilder()
            .maximumSize(10_000)
            .expireAfterWrite(LOCK_DURATION)
            .build();

    public LoginService(UserMapper userMapper, RoleMapper roleMapper, TokenService tokenService,
                        PermVersionService permVersionService, LoginAttemptMapper loginAttemptMapper,
                        SessionService sessionService) {
        this.userMapper = userMapper;
        this.roleMapper = roleMapper;
        this.tokenService = tokenService;
        this.permVersionService = permVersionService;
        this.loginAttemptMapper = loginAttemptMapper;
        this.sessionService = sessionService;
    }

    public LoginResult login(LoginRequest req) {
        String username = req.username().trim();
        HttpServletRequest request = currentRequest();

        UserEntity user = userMapper.selectOne(
                Wrappers.<UserEntity>lambdaQuery().eq(UserEntity::getUsername, username));

        // 已存在的账号：锁定状态以行为准。锁定中的账号连口令都不校验——
        // 让爆破者拿不到「这次是否猜对」的任何信号
        if (user != null && user.getLockedUntil() != null
                && user.getLockedUntil().isAfter(LocalDateTime.now())) {
            long remainMinutes = Math.max(1, Duration.between(LocalDateTime.now(),
                    user.getLockedUntil()).toMinutes());
            recordAttempt(username, user.getId(), false, "已锁定", request);
            throw new BizException(ErrorCode.CONFLICT,
                    "账号已临时锁定，请约 " + remainMinutes + " 分钟后再试");
        }
        // 不存在的用户名：内存计数兜底（锁没地方挂，也不能无限灌审计表）
        if (user == null) {
            Integer failed = unknownFailures.getIfPresent(username);
            if (failed != null && failed >= MAX_FAILURES) {
                throw new BizException(ErrorCode.CONFLICT,
                        "口令连续错误 " + MAX_FAILURES + " 次，请 " + LOCK_DURATION.toMinutes() + " 分钟后再试");
            }
        }

        // 用户不存在与口令错误返回同一句话：否则这个接口就成了用户名枚举器
        if (user == null || !passwordMatches(req.password(), user.getPasswordHash())) {
            recordAttempt(username, user == null ? null : user.getId(), false, "用户名或口令错误", request);
            if (user == null) {
                unknownFailures.put(username, unknownFailures.getIfPresent(username) == null
                        ? 1 : unknownFailures.getIfPresent(username) + 1);
            } else {
                bumpFailure(user);
            }
            throw new BizException(ErrorCode.BAD_REQUEST, "用户名或口令错误");
        }

        if (user.getEnabled() == null || user.getEnabled() == 0) {
            recordAttempt(username, user.getId(), false, "账号已停用", request);
            throw new BizException(ErrorCode.FORBIDDEN, "账号已停用");
        }

        // 成功：清计数与锁定，记最近登录（这些列专用于登录安全，别处不读）
        userMapper.update(null, Wrappers.<UserEntity>lambdaUpdate()
                .eq(UserEntity::getId, user.getId())
                .set(UserEntity::getFailedAttempts, 0)
                .set(UserEntity::getLockedUntil, null)
                .set(UserEntity::getLastLoginAt, LocalDateTime.now())
                .set(UserEntity::getLastLoginIp, SessionService.clientIp(request)));
        recordAttempt(username, user.getId(), true, null, request);
        unknownFailures.invalidate(username);
        // 登录是权威时刻：清掉可能过期的版本缓存，免得刚签发的令牌被判成「已变更」
        permVersionService.invalidate(user.getId());
        String token = tokenService.issue(user.getId(), user.getPermVersion());
        sessionService.record(user.getId(), token, request);
        return new LoginResult(token, user.getPermVersion(), toView(user));
    }

    /**
     * 当前用户。权限版本比对已上移到拦截器（PermVersionGuard），
     * 这里只负责组装视图——判定逻辑只有一处，免得两个地方口径不一致。
     */
    public MeResult me(long userId) {
        UserEntity user = requireEnabled(userId);
        RoleEntity role = roleMapper.selectById(user.getRoleId());
        if (role == null) {
            // 角色被删而用户还在，属于数据异常；拒绝比放行安全（权限 4.6：失败即拒绝）
            throw new BizException(ErrorCode.FORBIDDEN, "角色不存在");
        }
        return new MeResult(user.getId(), user.getUsername(), role.getCode(), role.getName(),
                role.getLevel(), user.getPermVersion(), isTrue(user.getMustChangePassword()));
    }

    /**
     * 改密：写新口令 → 清 must_change_password → perm_version 自增。
     * 版本一增，本令牌立刻失效，强制用新口令重新登录（权限 2 节）。
     */
    @Transactional
    public void changePassword(long userId, PasswordRequest req) {
        UserEntity user = requireEnabled(userId);
        if (!passwordMatches(req.oldPassword(), user.getPasswordHash())) {
            throw new BizException(ErrorCode.BAD_REQUEST, "原口令不正确");
        }
        if (req.oldPassword().equals(req.newPassword())) {
            throw new BizException(ErrorCode.BAD_REQUEST, "新口令不能与原口令相同");
        }
        userMapper.update(null, Wrappers.<UserEntity>lambdaUpdate()
                .eq(UserEntity::getId, userId)
                .set(UserEntity::getPasswordHash, BCrypt.hashpw(req.newPassword(), BCrypt.gensalt(10)))
                .set(UserEntity::getMustChangePassword, 0));
        // 版本号递增统一走 PermVersionService，不许各处自己写 UPDATE（接口清单 9.2）
        permVersionService.bumpUser(userId);
    }

    // ------------------------------------------------------------------ 内部

    /**
     * 失败计数与落锁。锁启动时计数清零：每个锁定周期重新数满 5 次，
     * 语义是「每 10 分钟至多试 5 次」，不是「累计 5 次永久锁」。
     * 并发双发最多把同一阈值写两遍，无害。
     */
    private void bumpFailure(UserEntity user) {
        int failed = (user.getFailedAttempts() == null ? 0 : user.getFailedAttempts()) + 1;
        if (failed >= MAX_FAILURES) {
            userMapper.update(null, Wrappers.<UserEntity>lambdaUpdate()
                    .eq(UserEntity::getId, user.getId())
                    .set(UserEntity::getFailedAttempts, 0)
                    .set(UserEntity::getLockedUntil, LocalDateTime.now().plus(LOCK_DURATION)));
            log.warn("账号已临时锁定：{}（连续失败 {} 次）", user.getUsername(), failed);
        } else {
            userMapper.update(null, Wrappers.<UserEntity>lambdaUpdate()
                    .eq(UserEntity::getId, user.getId())
                    .set(UserEntity::getFailedAttempts, failed));
        }
    }

    /** 登录尝试落审计表。失败绝不拦住登录本身——取证层不可靠时业务层照常运转 */
    private void recordAttempt(String username, Long userId, boolean success, String failReason,
                               HttpServletRequest request) {
        try {
            LoginAttemptEntity row = new LoginAttemptEntity();
            row.setUsername(username);
            row.setUserId(userId);
            row.setSuccess(success ? 1 : 0);
            row.setFailReason(failReason);
            row.setIp(SessionService.clientIp(request));
            row.setUserAgent(SessionService.userAgent(request));
            loginAttemptMapper.insert(row);
        } catch (RuntimeException e) {
            log.warn("登录尝试落库失败 username={}", username, e);
        }
    }

    private HttpServletRequest currentRequest() {
        if (RequestContextHolder.getRequestAttributes() instanceof ServletRequestAttributes attributes) {
            return attributes.getRequest();
        }
        return null;
    }

    private UserEntity requireEnabled(long userId) {
        UserEntity user = userMapper.selectById(userId);
        if (user == null) {
            throw new BizException(ErrorCode.TOKEN_STALE, "用户不存在，请重新登录");
        }
        if (user.getEnabled() == null || user.getEnabled() == 0) {
            throw new BizException(ErrorCode.FORBIDDEN, "账号已停用");
        }
        return user;
    }

    private UserView toView(UserEntity user) {
        RoleEntity role = roleMapper.selectById(user.getRoleId());
        return new UserView(user.getId(), user.getUsername(),
                role == null ? null : role.getCode(),
                user.getPermVersion(), isTrue(user.getMustChangePassword()));
    }

    private boolean passwordMatches(String raw, String hash) {
        if (hash == null || hash.isBlank()) {
            return false;
        }
        try {
            return BCrypt.checkpw(raw, hash);
        } catch (IllegalArgumentException e) {
            // 哈希格式不合法（例如手工改过库）：当作校验失败，不当 500
            log.warn("口令哈希格式不合法，按校验失败处理");
            return false;
        }
    }

    private boolean isTrue(Integer flag) {
        return flag != null && flag == 1;
    }
}
