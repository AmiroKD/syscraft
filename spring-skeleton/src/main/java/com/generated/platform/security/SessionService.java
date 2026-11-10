package com.generated.platform.security;

import com.baomidou.mybatisplus.core.toolkit.Wrappers;
import com.generated.platform.admin.entity.SessionEntity;
import com.generated.platform.admin.entity.UserEntity;
import com.generated.platform.admin.mapper.SessionMapper;
import com.generated.platform.admin.mapper.UserMapper;
import com.generated.platform.common.BizException;
import com.generated.platform.common.ErrorCode;
import com.github.benmanes.caffeine.cache.Cache;
import com.github.benmanes.caffeine.cache.Caffeine;
import jakarta.servlet.http.HttpServletRequest;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.stereotype.Service;

import java.nio.charset.StandardCharsets;
import java.security.MessageDigest;
import java.security.NoSuchAlgorithmException;
import java.time.Duration;
import java.time.LocalDateTime;
import java.util.HexFormat;
import java.util.List;

/**
 * 会话登记簿（底座扩展 v0.1）。
 *
 * <p><b>定位必须说清：</b>JWT 仍然无状态（{@code StpLogicJwtForStateless} 不动），
 * 本表不是会话存储，只做三件事——</p>
 * <ol>
 *   <li><b>在线可见</b>：管理员能看到谁在线、从哪个 IP、用什么客户端；</li>
 *   <li><b>强制下线的执行开关</b>：吊销按令牌哈希拦截请求（每个请求一次主键查询）。
 *       是<b>会话级</b>精确吊销——同一用户的其他令牌不受影响；
 *       若要全端下线，走停用账号（ perm_version 机制已覆盖）；</li>
 *   <li><b>活跃度信号</b>：last_active 支撑在线窗口判定，节流落库（一分钟至多一次）。</li>
 * </ol>
 *
 * <p>只存令牌的 SHA-256，不存明文——与 adm_confirm_token 同一纪律。</p>
 */
@Service
public class SessionService {

    private static final Logger log = LoggerFactory.getLogger(SessionService.class);

    /** 在线窗口：last_active 落在这个窗口内才算在线 */
    public static final Duration ONLINE_WINDOW = Duration.ofMinutes(30);

    /** 活跃时间节流：同一个令牌一分钟至多落库一次，避免每个请求都写 */
    private static final Duration TOUCH_THROTTLE = Duration.ofSeconds(60);

    private final SessionMapper sessionMapper;
    private final UserMapper userMapper;

    /** key = token_hash。命中说明一分钟内已写过活跃时间，本轮跳过 */
    private final Cache<String, Boolean> touchThrottle = Caffeine.newBuilder()
            .maximumSize(10_000)
            .expireAfterWrite(TOUCH_THROTTLE)
            .build();

    public SessionService(SessionMapper sessionMapper, UserMapper userMapper) {
        this.sessionMapper = sessionMapper;
        this.userMapper = userMapper;
    }

    /** 登录成功后登记。同一令牌重复登录（理论少见）按「更新活跃」处理，不报错 */
    public void record(long userId, String token, HttpServletRequest request) {
        try {
            SessionEntity row = new SessionEntity();
            row.setUserId(userId);
            row.setTokenHash(sha256Hex(token));
            row.setIp(clientIp(request));
            row.setUserAgent(userAgent(request));
            try {
                sessionMapper.insert(row);
            } catch (org.springframework.dao.DuplicateKeyException e) {
                // uk_session_token 命中：同令牌再次登录，刷新活跃即可
                sessionMapper.update(null, Wrappers.<SessionEntity>lambdaUpdate()
                        .eq(SessionEntity::getTokenHash, row.getTokenHash())
                        .set(SessionEntity::getLastActiveAt, LocalDateTime.now())
                        .set(SessionEntity::getRevoked, 0)
                        .set(SessionEntity::getRevokedAt, null)
                        .set(SessionEntity::getRevokedBy, null)
                        .set(SessionEntity::getRevokedReason, null));
            }
        } catch (RuntimeException e) {
            // 登记失败不能拦住登录，但要大声记日志——会话可见性缺一行不算事故，静默缺失才算
            log.warn("会话登记失败 userId={}", userId, e);
        }
    }

    /**
     * 每个受保护请求调用（在认证与权限版本比对之后）：已吊销即 401，未吊销做节流活跃更新。
     * 令牌在表中无记录时放行——兼容本功能上线前签发的存量令牌，自然过期淘汰。
     */
    public void checkAndTouch(String token) {
        String hash = sha256Hex(token);
        SessionEntity session = sessionMapper.selectOne(
                Wrappers.<SessionEntity>lambdaQuery().eq(SessionEntity::getTokenHash, hash));
        if (session == null) {
            return;
        }
        if (session.getRevoked() != null && session.getRevoked() == 1) {
            throw new BizException(ErrorCode.TOKEN_STALE, "会话已被管理员下线，请重新登录");
        }
        if (touchThrottle.asMap().putIfAbsent(hash, Boolean.TRUE) == null) {
            sessionMapper.update(null, Wrappers.<SessionEntity>lambdaUpdate()
                    .eq(SessionEntity::getId, session.getId())
                    .set(SessionEntity::getLastActiveAt, LocalDateTime.now()));
        }
    }

    /** 在线会话列表（带用户名）。可见性门槛由调用方的管理端点把守 */
    public record SessionView(long id, long userId, String username, String ip, String userAgent,
                              LocalDateTime createdAt, LocalDateTime lastActiveAt) {
    }

    public List<SessionView> listActive() {
        LocalDateTime since = LocalDateTime.now().minus(ONLINE_WINDOW);
        List<SessionEntity> rows = sessionMapper.selectList(Wrappers.<SessionEntity>lambdaQuery()
                .eq(SessionEntity::getRevoked, 0)
                .ge(SessionEntity::getLastActiveAt, since)
                .orderByDesc(SessionEntity::getLastActiveAt));
        return rows.stream().map(row -> {
            UserEntity user = userMapper.selectById(row.getUserId());
            return new SessionView(row.getId(), row.getUserId(),
                    user == null ? null : user.getUsername(),
                    row.getIp(), row.getUserAgent(), row.getCreatedAt(), row.getLastActiveAt());
        }).toList();
    }

    /**
     * 强制下线（会话级精确吊销）：置 revoked 后，该令牌在每个请求的
     * {@link #checkAndTouch} 被拒（401）。同用户的其他会话不受影响；
     * 全端失效走停用账号 / 改密（perm_version 机制，已有路径）。
     */
    public void forceLogout(long sessionId, long operatorId) {
        SessionEntity session = sessionMapper.selectById(sessionId);
        if (session == null) {
            throw new BizException(ErrorCode.NOT_FOUND, "会话不存在：" + sessionId);
        }
        sessionMapper.update(null, Wrappers.<SessionEntity>lambdaUpdate()
                .eq(SessionEntity::getId, sessionId)
                .set(SessionEntity::getRevoked, 1)
                .set(SessionEntity::getRevokedAt, LocalDateTime.now())
                .set(SessionEntity::getRevokedBy, operatorId)
                .set(SessionEntity::getRevokedReason, "管理员强制下线"));
        touchThrottle.invalidate(session.getTokenHash());
    }

    /** 清理：吊销或失联超过 7 天的登记行。由 {@code MaintenanceJobs} 定时调用 */
    public int cleanup() {
        LocalDateTime deadline = LocalDateTime.now().minus(Duration.ofDays(7));
        return sessionMapper.delete(Wrappers.<SessionEntity>lambdaQuery()
                .and(w -> w.eq(SessionEntity::getRevoked, 1).le(SessionEntity::getRevokedAt, deadline)
                        .or(s -> s.eq(SessionEntity::getRevoked, 0).le(SessionEntity::getLastActiveAt, deadline))));
    }

    // ------------------------------------------------------------------ 元信息与哈希

    public static String clientIp(HttpServletRequest request) {
        if (request == null) {
            return null;
        }
        String forwarded = request.getHeader("X-Forwarded-For");
        if (forwarded != null && !forwarded.isBlank()) {
            // 多级代理取第一段（最初客户端）；截断防异常头撑爆列
            String first = forwarded.split(",")[0].trim();
            return first.length() > 64 ? first.substring(0, 64) : first;
        }
        return request.getRemoteAddr();
    }

    public static String userAgent(HttpServletRequest request) {
        if (request == null) {
            return null;
        }
        String ua = request.getHeader("User-Agent");
        return ua == null ? null : (ua.length() > 255 ? ua.substring(0, 255) : ua);
    }

    public static String sha256Hex(String value) {
        try {
            MessageDigest digest = MessageDigest.getInstance("SHA-256");
            return HexFormat.of().formatHex(digest.digest(value.getBytes(StandardCharsets.UTF_8)));
        } catch (NoSuchAlgorithmException e) {
            // JVM 必带 SHA-256，这里只是把受检异常收进不受检面
            throw new IllegalStateException(e);
        }
    }
}
