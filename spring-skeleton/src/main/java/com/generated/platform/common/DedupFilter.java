package com.generated.platform.common;

import cn.dev33.satoken.stp.StpUtil;
import com.generated.platform.admin.entity.RequestDedupEntity;
import com.generated.platform.admin.mapper.RequestDedupMapper;
import com.generated.platform.security.PermVersionGuard;
import jakarta.servlet.FilterChain;
import jakarta.servlet.ServletException;
import jakarta.servlet.http.HttpServletRequest;
import jakarta.servlet.http.HttpServletResponse;
import org.springframework.stereotype.Component;
import org.springframework.web.filter.OncePerRequestFilter;
import org.springframework.web.util.ContentCachingResponseWrapper;

import java.io.IOException;
import java.nio.charset.StandardCharsets;
import java.time.LocalDateTime;
import java.util.regex.Pattern;

/**
 * 业务写端点的幂等回放（adm_request_dedup）：Agent 超时重试时带同一个
 * X-Request-Id 再发一次，直接回放首次响应，避免重复插入。
 *
 * <p>为什么是 Filter 不是 HandlerInterceptor：**拦截器包装不了响应体**——
 * preHandle 里 new 的 ContentCachingResponseWrapper 不会传给控制器，
 * 缓存恒为空（实测踩过）；只有 Filter 能把 wrapper 传进 chain。</p>
 *
 * <p>安全边界：回放路径不经认证拦截器，所以这里自带两道检查——
 * 已登录 + 权限版本比对。任一不过就放行给后面的认证链正常拒绝。</p>
 */
public class DedupFilter extends OncePerRequestFilter {

    public static final String HEADER = "X-Request-Id";
    private static final Pattern WRITE_PATH =
            Pattern.compile("^/api/biz/[^/]+/(insert|update|delete)$");
    /** 去重窗口。令牌 5 分钟、Agent 重试间隔秒级，10 分钟足够覆盖 */
    private static final int TTL_MINUTES = 10;

    private final RequestDedupMapper mapper;
    private final PermVersionGuard permVersionGuard;

    public DedupFilter(RequestDedupMapper mapper, PermVersionGuard permVersionGuard) {
        this.mapper = mapper;
        this.permVersionGuard = permVersionGuard;
    }

    @Override
    protected void doFilterInternal(HttpServletRequest request, HttpServletResponse response,
                                    FilterChain chain) throws ServletException, IOException {
        String requestId = request.getHeader(HEADER);
        boolean writePath = "POST".equalsIgnoreCase(request.getMethod())
                && WRITE_PATH.matcher(request.getRequestURI()).matches();
        if (requestId == null || requestId.isBlank() || requestId.length() > 64 || !writePath) {
            chain.doFilter(request, response);
            return;
        }

        // 回放前的身份检查：未登录 → 交给后面的认证链；已登录但版本过期 → 同理
        Object loginId = StpUtil.getLoginIdDefaultNull();
        if (loginId == null) {
            chain.doFilter(request, response);
            return;
        }
        Long userId;
        try {
            permVersionGuard.check(Long.parseLong(String.valueOf(loginId)));
            userId = Long.parseLong(String.valueOf(loginId));
        } catch (RuntimeException stale) {
            // 版本过期：放行给后面的认证链统一拒绝，不在这里改写响应语义
            chain.doFilter(request, response);
            return;
        }

        RequestDedupEntity stored = mapper.selectById(requestId);
        if (stored != null) {
            if (!stored.getExpiresAt().isAfter(LocalDateTime.now())) {
                // 过期：窗口已失效，同键重试是合法的新请求，清掉旧行再占坑
                mapper.deleteById(requestId);
            } else if (stored.getUserId() != null && !stored.getUserId().equals(userId)) {
                // 不同用户的同键请求互不相干：旧记录让位，本请求按新键处理
                mapper.deleteById(requestId);
            } else if (stored.getResponseJson() != null) {
                // 命中：首次响应原样回放，不进业务链——重试不再产生第二次写入
                response.setStatus(HttpServletResponse.SC_OK);
                response.setContentType("application/json;charset=UTF-8");
                response.getWriter().write(stored.getResponseJson());
                return;
            } else {
                // 占坑中（同键并发请求正在业务链里）：拒绝而不是再跑一遍业务
                response.setStatus(HttpServletResponse.SC_CONFLICT);
                response.setContentType("application/json;charset=UTF-8");
                response.getWriter().write(
                        "{\"code\":409,\"msg\":\"同 id 请求正在处理中，请稍后重试\",\"data\":null}");
                return;
            }
        }

        // 占坑：同键并发在主键上天然互斥，谁先插进去谁进业务链（数据库级原子性，
        // 不需要 JVM 内锁，重启也不丢窗口）
        RequestDedupEntity claim = new RequestDedupEntity();
        claim.setRequestId(requestId);
        claim.setUserId(userId);
        claim.setResponseJson(null);
        claim.setExpiresAt(LocalDateTime.now().plusMinutes(TTL_MINUTES));
        try {
            mapper.insert(claim);
        } catch (org.springframework.dao.DuplicateKeyException raced) {
            // 极窄竞态（查完到插之间别人占了坑）：重读后按命中/占坑处理
            RequestDedupEntity fresh = mapper.selectById(requestId);
            if (fresh != null && fresh.getResponseJson() != null
                    && fresh.getUserId() != null && fresh.getUserId().equals(userId)) {
                response.setStatus(HttpServletResponse.SC_OK);
                response.setContentType("application/json;charset=UTF-8");
                response.getWriter().write(fresh.getResponseJson());
            } else {
                response.setStatus(HttpServletResponse.SC_CONFLICT);
                response.setContentType("application/json;charset=UTF-8");
                response.getWriter().write(
                        "{\"code\":409,\"msg\":\"同 id 请求正在处理中，请稍后重试\",\"data\":null}");
            }
            return;
        }

        ContentCachingResponseWrapper wrapper = new ContentCachingResponseWrapper(response);
        try {
            chain.doFilter(request, wrapper);
            if (wrapper.getStatus() >= 200 && wrapper.getStatus() < 300) {
                // 成功：落响应原文，坑转正为可回放记录
                String body = new String(wrapper.getContentAsByteArray(), StandardCharsets.UTF_8);
                if (!body.isBlank()) {
                    claim.setResponseJson(body);
                    claim.setExpiresAt(LocalDateTime.now().plusMinutes(TTL_MINUTES));
                    mapper.updateById(claim);
                } else {
                    mapper.deleteById(requestId);  // 空响应没什么可回放的，不占窗口
                }
            } else {
                // 失败的请求不占去重窗口：改对参数重试是合法的
                mapper.deleteById(requestId);
            }
        } catch (RuntimeException | ServletException | IOException aborted) {
            // 业务链炸了同样不占窗口；先把坑清掉再让异常照常传播
            mapper.deleteById(requestId);
            throw aborted;
        } finally {
            wrapper.copyBodyToResponse();
        }
    }
}
