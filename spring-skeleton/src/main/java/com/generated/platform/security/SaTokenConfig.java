package com.generated.platform.security;

import cn.dev33.satoken.interceptor.SaInterceptor;
import cn.dev33.satoken.jwt.StpLogicJwtForStateless;
import cn.dev33.satoken.stp.StpLogic;
import com.generated.platform.admin.mapper.RequestDedupMapper;
import com.generated.platform.common.DedupFilter;
import com.generated.platform.security.SessionService;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.boot.web.servlet.FilterRegistrationBean;
import org.springframework.context.annotation.Bean;
import org.springframework.context.annotation.Configuration;
import org.springframework.web.servlet.config.annotation.InterceptorRegistry;
import org.springframework.web.servlet.config.annotation.WebMvcConfigurer;

/**
 * 鉴权装配。拦截器顺序有讲究，见 {@link AuthGuardInterceptor} 的类注释：
 * <ol>
 *   <li>AuthGuardInterceptor：认证 + 权限版本比对（先跑，401 优先于 403）；</li>
 *   <li>SaInterceptor：注解鉴权（@SaCheckPermission，后跑）。</li>
 * </ol>
 *
 * <p>写端点的幂等回放是 {@link DedupFilter}（Filter 而非拦截器：拦截器包装不了
 * 响应体，实测踩过），注册见下方 dedupFilter；回放前的身份与版本检查在
 * filter 内部自带，回放不给未认证请求套历史响应的机会。</p>
 *
 * <p>「能不能调这个接口」由每个方法上的
 * {@code @SaCheckPermission("biz:{entity}:{op}")} 决定（权限 5.1）。</p>
 */
@Configuration
public class SaTokenConfig implements WebMvcConfigurer {

    private final PermVersionGuard permVersionGuard;
    private final SessionService sessionService;

    /** JWT 密钥启动期 fail-fast：与 SecretBox 对 APP_SECRET 的口径对齐。
     * 密钥泄露即等同令牌可伪造，优先级更高——不能等到第一次签发才在深处炸。 */
    public SaTokenConfig(PermVersionGuard permVersionGuard, SessionService sessionService,
                         @Value("${sa-token.jwt-secret-key:}") String jwtSecret) {
        if (jwtSecret == null || jwtSecret.isBlank()) {
            throw new IllegalStateException("缺少 JWT_SECRET：无签名密钥时令牌可被任意伪造，拒绝启动");
        }
        if (jwtSecret.trim().length() < 32) {
            throw new IllegalStateException("JWT_SECRET 强度不足（至少 32 字符）：短密钥可被暴力枚举");
        }
        this.permVersionGuard = permVersionGuard;
        this.sessionService = sessionService;
    }

    /**
     * 注入 Stateless JWT 实现。必须声明成 Bean：Sa-Token 取 StpLogic 时按类型找，
     * 不注册就还是默认的内存会话实现，重启即全员掉线。
     */
    @Bean
    public StpLogic stpLogicJwt() {
        return new StpLogicJwtForStateless();
    }

    @Override
    public void addInterceptors(InterceptorRegistry registry) {
        registry.addInterceptor(new AuthGuardInterceptor(permVersionGuard, sessionService))
                .addPathPatterns("/api/**")
                // 登录接口本身不能要求登录；actuator 不在 /api 下，天然放行
                .excludePathPatterns("/api/auth/login");

        // 无参构造：只做注解鉴权，不带额外的 auth 函数
        registry.addInterceptor(new SaInterceptor())
                .addPathPatterns("/api/**")
                .excludePathPatterns("/api/auth/login");
    }

    @Bean
    public FilterRegistrationBean<DedupFilter> dedupFilter(RequestDedupMapper dedupMapper,
                                                           PermVersionGuard guard) {
        FilterRegistrationBean<DedupFilter> registration =
                new FilterRegistrationBean<>(new DedupFilter(dedupMapper, guard));
        registration.addUrlPatterns("/api/biz/*");
        registration.setOrder(50);
        return registration;
    }
}
