package com.generated.platform.admin;

import com.generated.platform.admin.dto.AdminDtos.CreateRoleRequest;
import com.generated.platform.admin.dto.AdminDtos.RoleView;
import com.generated.platform.admin.dto.AdminDtos.UpdateRoleRequest;
import com.generated.platform.common.ApiResponse;
import jakarta.validation.Valid;
import org.springframework.web.bind.annotation.DeleteMapping;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.PatchMapping;
import org.springframework.web.bind.annotation.PathVariable;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.RequestBody;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RestController;

import java.util.List;

@RestController
@RequestMapping("/api/admin/roles")
public class RoleController {

    private final RoleService roleService;

    public RoleController(RoleService roleService) {
        this.roleService = roleService;
    }

    @GetMapping
    public ApiResponse<List<RoleView>> list() {
        return ApiResponse.ok(roleService.list());
    }

    @PostMapping
    public ApiResponse<RoleView> create(@Valid @RequestBody CreateRoleRequest request) {
        return ApiResponse.ok(roleService.create(request));
    }

    @PatchMapping("/{id}")
    public ApiResponse<RoleView> update(@PathVariable long id, @Valid @RequestBody UpdateRoleRequest request) {
        return ApiResponse.ok(roleService.update(id, request));
    }

    @DeleteMapping("/{id}")
    public ApiResponse<Void> delete(@PathVariable long id) {
        roleService.delete(id);
        return ApiResponse.ok();
    }
}
