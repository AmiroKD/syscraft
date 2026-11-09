package com.generated.platform.auth.dto;

/**
 * 当前用户。比 {@link UserView} 多带上角色名与层级——界面上要显示「超级管理员」而不是 SUPER_ADMIN，
 * 生成模式入口的显隐也依赖层级 100。
 */
public record MeResult(Long id, String username, String roleCode, String roleName, int level,
                       int permVersion, boolean mustChangePassword) {
}
