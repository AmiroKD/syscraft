package com.generated.platform.admin;

import com.baomidou.mybatisplus.core.toolkit.Wrappers;
import com.generated.platform.admin.entity.RoleCapabilityEntity;
import com.generated.platform.admin.entity.RoleEntity;
import com.generated.platform.admin.entity.UserEntity;
import com.generated.platform.admin.mapper.RoleCapabilityMapper;
import com.generated.platform.admin.mapper.RoleMapper;
import com.generated.platform.admin.mapper.UserMapper;
import com.generated.platform.common.BizException;
import com.generated.platform.common.ErrorCode;
import cn.dev33.satoken.stp.StpUtil;
import org.springframework.stereotype.Component;

import java.util.HashSet;
import java.util.List;
import java.util.Set;

/**
 * 当前操作者：身份 + 角色 + 能力集合。管理接口的每一次判断都以它为准。
 *
 * <p>刻意<b>不</b>从令牌里读角色与能力：令牌只带 userId 与权限版本（权限 3 节），
 * 角色随时可能被改，判定必须回到数据库。</p>
 */
@Component
public class OperatorContext {

    /** 九种封闭能力，与 adm_role_capability 的 ENUM 一致 */
    public static final Set<String> ALL_CAPABILITIES = Set.of(
            "QUERY", "INSERT", "UPDATE", "DELETE", "GRANT",
            "MANAGE_USER", "MANAGE_ROLE", "CREATE_TABLE", "ALTER_TABLE");

    private final UserMapper userMapper;
    private final RoleMapper roleMapper;
    private final RoleCapabilityMapper capabilityMapper;

    public OperatorContext(UserMapper userMapper, RoleMapper roleMapper,
                           RoleCapabilityMapper capabilityMapper) {
        this.userMapper = userMapper;
        this.roleMapper = roleMapper;
        this.capabilityMapper = capabilityMapper;
    }

    public record Operator(long userId, long roleId, String roleCode, int roleLevel, Set<String> capabilities) {

        public boolean isSuperAdmin() {
            return "SUPER_ADMIN".equals(roleCode);
        }

        public boolean has(String capability) {
            return capabilities.contains(capability);
        }
    }

    public Operator current() {
        long userId = StpUtil.getLoginIdAsLong();
        UserEntity user = userMapper.selectById(userId);
        if (user == null || user.getEnabled() == null || user.getEnabled() == 0) {
            throw new BizException(ErrorCode.TOKEN_STALE, "用户不存在或已停用，请重新登录");
        }
        RoleEntity role = roleMapper.selectById(user.getRoleId());
        if (role == null) {
            throw new BizException(ErrorCode.FORBIDDEN, "角色不存在");
        }
        return new Operator(userId, role.getId(), role.getCode(), role.getLevel(), capabilitiesOf(role.getId()));
    }

    /** 能力门槛。缺能力一律 403，并且错误信息里带上缺的是哪一项，便于排查。 */
    public Operator require(String capability, String action) {
        assertCapability(capability);
        Operator operator = current();
        if (!operator.has(capability)) {
            throw new BizException(ErrorCode.FORBIDDEN, "无权限" + action + "（缺少能力 " + capability + "）");
        }
        return operator;
    }

    public Set<String> capabilitiesOf(Long roleId) {
        List<RoleCapabilityEntity> rows = capabilityMapper.selectList(
                Wrappers.<RoleCapabilityEntity>lambdaQuery().eq(RoleCapabilityEntity::getRoleId, roleId));
        Set<String> capabilities = new HashSet<>();
        for (RoleCapabilityEntity row : rows) {
            if (row.getCapability() != null) {
                capabilities.add(row.getCapability());
            }
        }
        return capabilities;
    }

    public void assertCapability(String capability) {
        if (!ALL_CAPABILITIES.contains(capability)) {
            throw new BizException(ErrorCode.BAD_REQUEST, "未知能力：" + capability);
        }
    }
}
