package com.generated.platform.auth.dto;

/**
 * 登录结果。token 只在这一次出现明文，服务端不存。
 *
 * @param token       访问令牌，放 Authorization: Bearer 里
 * @param permVersion 权限版本，随任何角色/能力/状态变更自增
 * @param user        用户视图
 */
public record LoginResult(String token, int permVersion, UserView user) {
}
