package com.generated.platform.auth.dto;

import jakarta.validation.constraints.NotBlank;
import jakarta.validation.constraints.Size;

public record LoginRequest(
        @NotBlank(message = "不能为空") @Size(max = 64) String username,
        @NotBlank(message = "不能为空") @Size(max = 64) String password) {
}
