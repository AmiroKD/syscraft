package com.generated.platform.admin;

import com.generated.platform.admin.dto.AdminDtos.AdminUserView;
import com.generated.platform.admin.dto.AdminDtos.CreateUserRequest;
import com.generated.platform.admin.dto.AdminDtos.ResetPasswordResult;
import com.generated.platform.admin.dto.AdminDtos.UpdateUserRequest;
import com.generated.platform.common.ApiResponse;
import com.generated.platform.common.PageResult;
import jakarta.validation.Valid;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.PatchMapping;
import org.springframework.web.bind.annotation.PathVariable;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.RequestBody;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RequestParam;
import org.springframework.web.bind.annotation.RestController;

/**
 * 账号管理接口（契约 2.3）。Controller 只做参数绑定，
 * 委托校验与能力门槛都在 Service——管理接口会被「对话工具」和未来的批量导入两条路调用，
 * 校验放在 Controller 会漏（接口清单 9.4）。
 */
@RestController
@RequestMapping("/api/admin/users")
public class UserController {

    private final UserService userService;

    public UserController(UserService userService) {
        this.userService = userService;
    }

    @GetMapping
    public ApiResponse<PageResult<AdminUserView>> list(
            @RequestParam(required = false) String keyword,
            @RequestParam(required = false) String roleCode,
            @RequestParam(required = false) Boolean enabled,
            @RequestParam(required = false) Integer page,
            @RequestParam(required = false) Integer size) {
        return ApiResponse.ok(userService.list(keyword, roleCode, enabled, page, size));
    }

    @PostMapping
    public ApiResponse<AdminUserView> create(@Valid @RequestBody CreateUserRequest request) {
        return ApiResponse.ok(userService.create(request));
    }

    @PatchMapping("/{id}")
    public ApiResponse<Void> update(@PathVariable long id, @Valid @RequestBody UpdateUserRequest request) {
        userService.update(id, request);
        return ApiResponse.ok();
    }

    @PostMapping("/{id}/password")
    public ApiResponse<ResetPasswordResult> resetPassword(@PathVariable long id) {
        return ApiResponse.ok(userService.resetPassword(id));
    }
}
