package com.generated.platform.security;

import cn.dev33.satoken.stp.StpInterface;
import org.springframework.stereotype.Component;

import java.util.List;

/**
 * Sa-Token 的权限码来源。{@code @SaCheckPermission("biz:order:read")} 最终问到这里。
 *
 * <p>这个类只做一件事：把 loginId 转成 userId 后交给 {@link PermissionCodeResolver}。
 * 判定规则一行都不写在这里——否则「权限码怎么来的」就有两个出处，审计时说不清。</p>
 */
@Component
public class StpInterfaceImpl implements StpInterface {

    private final PermissionCodeResolver resolver;

    public StpInterfaceImpl(PermissionCodeResolver resolver) {
        this.resolver = resolver;
    }

    @Override
    public List<String> getPermissionList(Object loginId, String loginType) {
        if (loginId == null) {
            // 失败即拒绝：返回空集合而不是抛异常后放行
            return List.of();
        }
        try {
            return resolver.resolve(Long.parseLong(loginId.toString()));
        } catch (NumberFormatException e) {
            return List.of();
        }
    }

    /**
     * 不使用角色级注解。<b>角色为单值且随时可变</b>，把角色写进鉴权表达式会让
     * 「新增一个同权限角色」变成改代码；权限码才是唯一判据（权限 4.6 配套第 1 条）。
     */
    @Override
    public List<String> getRoleList(Object loginId, String loginType) {
        return List.of();
    }
}
