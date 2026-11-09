package com.generated.platform.auth;

import com.baomidou.mybatisplus.core.toolkit.Wrappers;
import com.generated.platform.admin.entity.RoleEntity;
import com.generated.platform.admin.entity.UserEntity;
import com.generated.platform.admin.mapper.RoleMapper;
import com.generated.platform.admin.mapper.UserMapper;
import com.generated.platform.auth.dto.LoginRequest;
import com.generated.platform.auth.dto.LoginResult;
import com.generated.platform.auth.dto.MeResult;
import com.generated.platform.auth.dto.PasswordRequest;
import com.generated.platform.auth.dto.UserView;
import com.generated.platform.common.BizException;
import com.generated.platform.security.PermVersionService;
import com.generated.platform.common.ErrorCode;
import com.github.benmanes.caffeine.cache.Cache;
import com.github.benmanes.caffeine.cache.Caffeine;
import cn.dev33.satoken.secure.BCrypt;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;

import java.time.Duration;

/**
 * 登录、当前用户、改密。
 *
 * <p>口令校验用 Sa-Token 自带的 BCrypt（jBCrypt 实现），不额外引入 spring-security-crypto，
 * 依赖清单保持在七个以内。</p>
 */
@Service
public class LoginService {

    private static final Logger log = LoggerFactory.getLogger(LoginService.class);

    /** 连续失败 5 次锁 10 分钟（接口清单 9.5：用内存计数，写库是写放大，且重启可绕过） */
    private static final int MAX_FAILURES = 5;
    private static final Duration LOCK_DURATION = Duration.ofMinutes(10);

    private final UserMapper userMapper;
    private final RoleMapper roleMapper;
    private final TokenService tokenService;
    private final PermVersionService permVersionService;

    /** key = username。故意按用户名而非按 IP：同一台机器上的多个账号互不影响 */
    private final Cache<String, Integer> failures = Caffeine.newBuilder()
            .maximumSize(10_000)
            .expireAfterWrite(LOCK_DURATION)
            .build();

    public LoginService(UserMapper userMapper, RoleMapper roleMapper, TokenService tokenService,
                        PermVersionService permVersionService) {
        this.userMapper = userMapper;
        this.roleMapper = roleMapper;
        this.tokenService = tokenService;
        this.permVersionService = permVersionService;
    }

    public LoginResult login(LoginRequest req) {
        String username = req.username().trim();

        Integer failed = failures.getIfPresent(username);
        if (failed != null && failed >= MAX_FAILURES) {
            throw new BizException(ErrorCode.CONFLICT,
                    "口令连续错误 " + MAX_FAILURES + " 次，请 " + LOCK_DURATION.toMinutes() + " 分钟后再试");
        }

        UserEntity user = userMapper.selectOne(
                Wrappers.<UserEntity>lambdaQuery().eq(UserEntity::getUsername, username));

        // 用户不存在与口令错误返回同一句话：否则这个接口就成了用户名枚举器
        if (user == null || !passwordMatches(req.password(), user.getPasswordHash())) {
            failures.put(username, failed == null ? 1 : failed + 1);
            throw new BizException(ErrorCode.BAD_REQUEST, "用户名或口令错误");
        }

        if (user.getEnabled() == null || user.getEnabled() == 0) {
            throw new BizException(ErrorCode.FORBIDDEN, "账号已停用");
        }

        failures.invalidate(username);
        // 登录是权威时刻：清掉可能过期的版本缓存，免得刚签发的令牌被判成「已变更」
        permVersionService.invalidate(user.getId());
        String token = tokenService.issue(user.getId(), user.getPermVersion());
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
                .set(UserEntity::getMustChangePassword, 0)
                // 在 SQL 里自增而不是读改写：避免并发改密时版本号回退
                .setSql("perm_version = perm_version + 1"));
        permVersionService.invalidate(userId);
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
