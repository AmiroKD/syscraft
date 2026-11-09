package com.generated.platform.admin;

import com.baomidou.mybatisplus.core.toolkit.Wrappers;
import com.generated.platform.admin.OperatorContext.Operator;
import com.generated.platform.admin.dto.AdminDtos.CreateRoleRequest;
import com.generated.platform.admin.dto.AdminDtos.RoleView;
import com.generated.platform.admin.dto.AdminDtos.UpdateRoleRequest;
import com.generated.platform.admin.entity.RoleCapabilityEntity;
import com.generated.platform.admin.entity.RoleEntity;
import com.generated.platform.admin.entity.UserEntity;
import com.generated.platform.admin.mapper.RoleCapabilityMapper;
import com.generated.platform.admin.mapper.RoleMapper;
import com.generated.platform.admin.mapper.TableGrantMapper;
import com.generated.platform.admin.mapper.UserMapper;
import com.generated.platform.common.BizException;
import com.generated.platform.common.ErrorCode;
import com.generated.platform.security.PermVersionService;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;

import java.util.ArrayList;
import java.util.HashSet;
import java.util.LinkedHashSet;
import java.util.List;
import java.util.Map;
import java.util.Set;
import java.util.stream.Collectors;

/**
 * 角色管理（契约 2.3 的 4 个端点）。
 *
 * <p>能力集合可配、能力种类封闭：{@link OperatorContext#ALL_CAPABILITIES} 之外的字符串一律拒绝。
 * 建表/改表在 {@link DelegationGuard#checkGrantable} 里被硬拦，任何角色都授不出去。</p>
 */
@Service
public class RoleService {

    private final RoleMapper roleMapper;
    private final RoleCapabilityMapper capabilityMapper;
    private final UserMapper userMapper;
    private final TableGrantMapper grantMapper;
    private final OperatorContext operatorContext;
    private final DelegationGuard delegationGuard;
    private final PermVersionService permVersionService;

    public RoleService(RoleMapper roleMapper, RoleCapabilityMapper capabilityMapper, UserMapper userMapper,
                       TableGrantMapper grantMapper, OperatorContext operatorContext,
                       DelegationGuard delegationGuard, PermVersionService permVersionService) {
        this.roleMapper = roleMapper;
        this.capabilityMapper = capabilityMapper;
        this.userMapper = userMapper;
        this.grantMapper = grantMapper;
        this.operatorContext = operatorContext;
        this.delegationGuard = delegationGuard;
        this.permVersionService = permVersionService;
    }

    @Transactional(readOnly = true)
    public List<RoleView> list() {
        operatorContext.require("MANAGE_ROLE", "查看角色");

        Map<Long, List<String>> capsByRole = capabilityMapper.selectList(null).stream()
                .collect(Collectors.groupingBy(RoleCapabilityEntity::getRoleId,
                        Collectors.mapping(RoleCapabilityEntity::getCapability, Collectors.toList())));

        return roleMapper.selectList(Wrappers.<RoleEntity>lambdaQuery().orderByDesc(RoleEntity::getLevel))
                .stream()
                .map(role -> toView(role, capsByRole.getOrDefault(role.getId(), List.of())))
                .toList();
    }

    @Transactional
    public RoleView create(CreateRoleRequest request) {
        Operator operator = operatorContext.require("MANAGE_ROLE", "创建角色");
        Set<String> capabilities = normalize(request.capabilities());

        delegationGuard.checkRoleCreatable(operator, request.level());
        delegationGuard.checkGrantable(operator, capabilities);

        Long exists = roleMapper.selectCount(Wrappers.<RoleEntity>lambdaQuery()
                .eq(RoleEntity::getCode, request.code().trim()));
        if (exists != null && exists > 0) {
            throw new BizException(ErrorCode.CONFLICT, "角色标识已存在：" + request.code());
        }

        RoleEntity role = new RoleEntity();
        role.setCode(request.code().trim());
        role.setName(request.name().trim());
        role.setLevel(request.level());
        role.setBuiltin(0);
        roleMapper.insert(role);
        replaceCapabilities(role.getId(), capabilities);
        return toView(role, new ArrayList<>(capabilities));
    }

    /**
     * 改名称与能力集合。<b>层级不在可改范围内</b>：层级是委托体系的结构，
     * 允许改层级等于允许把任意角色抬到接近超管的位置。
     */
    @Transactional
    public RoleView update(long id, UpdateRoleRequest request) {
        Operator operator = operatorContext.require("MANAGE_ROLE", "修改角色");
        RoleEntity role = requireRole(id);
        delegationGuard.checkRoleEditable(operator, role);

        if (request.name() != null && !request.name().isBlank()) {
            role.setName(request.name().trim());
            roleMapper.updateById(role);
        }

        if (request.capabilities() != null) {
            Set<String> capabilities = normalize(request.capabilities());
            delegationGuard.checkGrantable(operator, capabilities);
            replaceCapabilities(id, capabilities);
            // 角色能力一变，该角色下所有人的权限码都变
            permVersionService.bumpRole(id);
            return toView(role, new ArrayList<>(capabilities));
        }
        return toView(role, new ArrayList<>(operatorContext.capabilitiesOf(id)));
    }

    @Transactional
    public void delete(long id) {
        Operator operator = operatorContext.require("MANAGE_ROLE", "删除角色");
        RoleEntity role = requireRole(id);
        delegationGuard.checkRoleEditable(operator, role);

        if (role.getBuiltin() != null && role.getBuiltin() == 1) {
            throw new BizException(ErrorCode.FORBIDDEN, "内置角色不可删除：" + role.getCode());
        }

        Long users = userMapper.selectCount(Wrappers.<UserEntity>lambdaQuery().eq(UserEntity::getRoleId, id));
        if (users != null && users > 0) {
            // 删除前迁移：宁可让管理员多一步，也不要留下角色为空的账号
            throw new BizException(ErrorCode.CONFLICT, "该角色下仍有 " + users + " 个用户，请先迁移");
        }

        capabilityMapper.delete(Wrappers.<RoleCapabilityEntity>lambdaQuery().eq(RoleCapabilityEntity::getRoleId, id));
        grantMapper.delete(Wrappers.<com.generated.platform.admin.entity.TableGrantEntity>lambdaQuery()
                .eq(com.generated.platform.admin.entity.TableGrantEntity::getSubjectType, "ROLE")
                .eq(com.generated.platform.admin.entity.TableGrantEntity::getSubjectId, id));
        roleMapper.deleteById(id);
        permVersionService.invalidateAll();
    }

    // ------------------------------------------------------------------ 内部

    private RoleEntity requireRole(long id) {
        RoleEntity role = roleMapper.selectById(id);
        if (role == null) {
            throw new BizException(ErrorCode.NOT_FOUND, "角色不存在：" + id);
        }
        return role;
    }

    private Set<String> normalize(List<String> capabilities) {
        if (capabilities == null) {
            return Set.of();
        }
        Set<String> result = new LinkedHashSet<>();
        for (String capability : capabilities) {
            if (capability == null || capability.isBlank()) {
                continue;
            }
            String normalized = capability.trim().toUpperCase();
            operatorContext.assertCapability(normalized);
            result.add(normalized);
        }
        return result;
    }

    private void replaceCapabilities(long roleId, Set<String> capabilities) {
        capabilityMapper.delete(Wrappers.<RoleCapabilityEntity>lambdaQuery()
                .eq(RoleCapabilityEntity::getRoleId, roleId));
        for (String capability : capabilities) {
            RoleCapabilityEntity row = new RoleCapabilityEntity();
            row.setRoleId(roleId);
            row.setCapability(capability);
            capabilityMapper.insert(row);
        }
    }

    private RoleView toView(RoleEntity role, List<String> capabilities) {
        Set<String> sorted = new HashSet<>(capabilities);
        return new RoleView(role.getId(), role.getCode(), role.getName(),
                role.getLevel() == null ? 0 : role.getLevel(),
                role.getBuiltin() != null && role.getBuiltin() == 1,
                sorted.stream().sorted().toList());
    }
}
