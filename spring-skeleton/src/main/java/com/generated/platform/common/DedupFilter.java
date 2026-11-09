package com.generated.platform.common;

import cn.dev33.satoken.stp.StpUtil;
import com.baomidou.mybatisplus.core.toolkit.Wrappers;
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
        try {
            permVersionGuard.check(Long.parseLong(String.valueOf(loginId)));
        } catch (RuntimeException stale) {
            // 版本过期：放行给后面的认证链统一拒绝，不在这里改写响应语义
            chain.doFilter(request, response);
            return;
        }

        RequestDedupEntity stored = mapper.selectById(requestId);
        if (stored != null && stored.getExpiresAt().isAfter(LocalDateTime.now())) {
            // 命中：首次响应原样回放，不进业务链——重试不再产生第二次写入
            response.setStatus(HttpServletResponse.SC_OK);
            response.setContentType("application/json;charset=UTF-8");
            response.getWriter().write(stored.getResponseJson());
            return;
        }

        ContentCachingResponseWrapper wrapper = new ContentCachingResponseWrapper(response);
        chain.doFilter(request, wrapper);
        try {
            if (wrapper.getStatus() >= 200 && wrapper.getStatus() < 300) {
                // 失败的请求不占去重窗口：改对参数重试是合法的
                mapper.delete(Wrappers.<RequestDedupEntity>lambdaQuery()
                        .lt(RequestDedupEntity::getExpiresAt, LocalDateTime.now()));
                RequestDedupEntity entity = new RequestDedupEntity();
                entity.setRequestId(requestId);
                entity.setResponseJson(new String(wrapper.getContentAsByteArray(), StandardCharsets.UTF_8));
                entity.setExpiresAt(LocalDateTime.now().plusMinutes(TTL_MINUTES));
                if (!entity.getResponseJson().isBlank()) {
                    mapper.insert(entity);
                }
            }
        } finally {
            wrapper.copyBodyToResponse();
        }
    }
}
