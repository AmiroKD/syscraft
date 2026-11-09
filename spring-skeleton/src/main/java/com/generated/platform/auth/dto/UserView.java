package com.generated.platform.auth.dto;

/**
 * 用户视图：登录结果与 /api/auth/me 共用一份，避免两处字段漂移。
 *
 * @param mustChangePassword 1=首登必须改密，前端据此直接弹改密框
 */
public record UserView(Long id, String username, String roleCode, int permVersion, boolean mustChangePassword) {
}
