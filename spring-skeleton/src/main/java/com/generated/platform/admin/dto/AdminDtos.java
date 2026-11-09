package com.generated.platform.admin.dto;

import jakarta.validation.constraints.NotBlank;
import jakarta.validation.constraints.NotNull;
import jakarta.validation.constraints.Size;

import java.time.LocalDateTime;
import java.util.List;

/**
 * 管理接口的出入参。集中一处，避免每个 Controller 各定义一套同名字段。
 *
 * <p>contract 里 createUser 带 displayName，但 adm_user 没有该列（权限 4.8 的字段清单里也没有），
 * 这里保留字段以兼容调用方，暂不落库。</p>
 */
public final class AdminDtos {

    private AdminDtos() {
    }

    public record CreateUserRequest(
            @NotBlank(message = "不能为空") @Size(max = 64) String username,
            @NotBlank(message = "不能为空") @Size(min = 8, max = 64, message = "长度需在 8-64 之间") String password,
            @NotBlank(message = "不能为空") String roleCode,
            String displayName) {
    }

    public record UpdateUserRequest(Boolean enabled, String roleCode) {
    }

    public record ResetPasswordResult(String username, String initialPassword) {
    }

    public record AdminUserView(Long id, String username, String roleCode, boolean enabled,
                                int permVersion, boolean mustChangePassword, LocalDateTime createdAt) {
    }

    public record CreateRoleRequest(
            @NotBlank(message = "不能为空") @Size(max = 32) String code,
            @NotBlank(message = "不能为空") @Size(max = 64) String name,
            @NotNull(message = "不能为空") Integer level,
            List<String> capabilities) {
    }

    /** 改层级不允许：层级是结构不是配置，所以这里根本没有 level 字段 */
    public record UpdateRoleRequest(@Size(max = 64) String name, List<String> capabilities) {
    }

    public record RoleView(Long id, String code, String name, int level, boolean builtin, List<String> capabilities) {
    }

    public record GrantRequest(
            @NotBlank(message = "不能为空") String subjectType,
            @NotNull(message = "不能为空") Long subjectId,
            @NotBlank(message = "不能为空") String entity,
            @NotBlank(message = "不能为空") String access) {
    }

    public record GrantView(Long id, String subjectType, Long subjectId, String entity,
                            String access, Long grantedBy) {
    }
}
