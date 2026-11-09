package com.generated.platform.meta;

import com.generated.platform.admin.entity.RoleEntity;
import com.generated.platform.admin.entity.UserEntity;
import com.generated.platform.admin.mapper.RoleMapper;
import com.generated.platform.admin.mapper.UserMapper;
import com.generated.platform.common.ApiResponse;
import com.generated.platform.common.BizException;
import com.generated.platform.common.ErrorCode;
import cn.dev33.satoken.stp.StpUtil;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RestController;

/**
 * 结构快照查询（契约 2.4）。
 *
 * <p>全量快照含所有表与字段，只给超管：生成模式只有超管有入口，
 * 而这份快照正是 Agent 在生成模式下认知系统的输入（权限 3 节）。</p>
 */
@RestController
@RequestMapping("/api/meta")
public class MetaController {

    private static final String SUPER_ADMIN_CODE = "SUPER_ADMIN";

    private final SnapshotService snapshotService;
    private final UserMapper userMapper;
    private final RoleMapper roleMapper;

    public MetaController(SnapshotService snapshotService, UserMapper userMapper, RoleMapper roleMapper) {
        this.snapshotService = snapshotService;
        this.userMapper = userMapper;
        this.roleMapper = roleMapper;
    }

    @GetMapping("/snapshot")
    public ApiResponse<SnapshotView> snapshot() {
        requireSuperAdmin();
        return ApiResponse.ok(snapshotService.snapshot());
    }

    private void requireSuperAdmin() {
        UserEntity user = userMapper.selectById(StpUtil.getLoginIdAsLong());
        RoleEntity role = user == null ? null : roleMapper.selectById(user.getRoleId());
        if (role == null || !SUPER_ADMIN_CODE.equals(role.getCode())) {
            throw new BizException(ErrorCode.FORBIDDEN, "仅超管可读取全量结构快照");
        }
    }
}
