package com.generated.platform.security;

import cn.dev33.satoken.interceptor.SaInterceptor;
import cn.dev33.satoken.jwt.StpLogicJwtForStateless;
import cn.dev33.satoken.stp.StpLogic;
import cn.dev33.satoken.stp.StpUtil;
import org.springframework.context.annotation.Bean;
import org.springframework.context.annotation.Configuration;
import org.springframework.web.servlet.config.annotation.InterceptorRegistry;
import org.springframework.web.servlet.config.annotation.WebMvcConfigurer;

/**
 * 鉴权装配。
 *
 * <p>这里只做「有没有登录」；「能不能调这个接口」由每个 Controller 方法上的
 * {@code @SaCheckPermission("biz:{entity}:{op}")} 决定（权限 5.1，模板保证不漏注解），
 * 「能碰哪条记录、哪个字段」由 DataScope 切面决定。三者各管一段，不能互相替代。</p>
 */
@Configuration
public class SaTokenConfig implements WebMvcConfigurer {

    private final PermVersionGuard permVersionGuard;

    public SaTokenConfig(PermVersionGuard permVersionGuard) {
        this.permVersionGuard = permVersionGuard;
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
        // 顺序固定：先认证（令牌有效性），再比对权限版本（是否被降权/改密作废）
        registry.addInterceptor(new SaInterceptor(handle -> {
                    StpUtil.checkLogin();
                    permVersionGuard.check();
                }))
                .addPathPatterns("/api/**")
                // 登录接口本身不能要求登录；actuator 不在 /api 下，天然放行
                .excludePathPatterns("/api/auth/login");
    }
}
