package com.generated.platform.auth.dto;

import jakarta.validation.constraints.NotBlank;
import jakarta.validation.constraints.Size;

public record PasswordRequest(
        @NotBlank(message = "不能为空") String oldPassword,
        @NotBlank(message = "不能为空") @Size(min = 8, max = 64, message = "长度需在 8-64 之间") String newPassword) {
}
