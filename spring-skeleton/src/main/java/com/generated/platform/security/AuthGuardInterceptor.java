package com.generated.platform.security;

import cn.dev33.satoken.stp.StpUtil;
import jakarta.servlet.http.HttpServletRequest;
import jakarta.servlet.http.HttpServletResponse;
import org.springframework.web.servlet.HandlerInterceptor;

/**
 * 认证与权限版本比对，<b>必须先于注解鉴权执行</b>。
 *
 * <p>SaInterceptor 的顺序是「先处理注解、再跑 auth 函数」：把 checkLogin 和版本比对
 * 写进 SaInterceptor 的 auth 函数里，权限不足（403）就会先于版本比对抛出——
 * 「令牌被改密作废」的用户看到的是「无此操作权限」，前端无法区分该跳登录页还是留在原地。
 * 契约要的语义是：没登录/被作废 → 401，权限不足 → 403，两者不许互相掩盖。</p>
 */
public class AuthGuardInterceptor implements HandlerInterceptor {

    private final PermVersionGuard permVersionGuard;
    private final SessionService sessionService;

    public AuthGuardInterceptor(PermVersionGuard permVersionGuard, SessionService sessionService) {
        this.permVersionGuard = permVersionGuard;
        this.sessionService = sessionService;
    }

    @Override
    public boolean preHandle(HttpServletRequest request, HttpServletResponse response, Object handler) {
        StpUtil.checkLogin();
        permVersionGuard.check();
        // 会话吊销校验 + 节流活跃登记（底座扩展 v0.1）。
        // 排在版本比对之后：被吊销的会话也该看到「请重新登录」而不是「无权限」
        sessionService.checkAndTouch(StpUtil.getTokenValue());
        return true;
    }
}
