package com.generated.platform.admin;

import com.baomidou.mybatisplus.core.conditions.update.LambdaUpdateWrapper;
import com.baomidou.mybatisplus.core.toolkit.Wrappers;
import cn.dev33.satoken.secure.BCrypt;
import com.generated.platform.admin.OperatorContext.Operator;
import com.generated.platform.admin.dto.AdminDtos.AdminUserView;
import com.generated.platform.admin.dto.AdminDtos.CreateUserRequest;
import com.generated.platform.admin.dto.AdminDtos.ResetPasswordResult;
import com.generated.platform.admin.dto.AdminDtos.UpdateUserRequest;
import com.generated.platform.admin.entity.RoleEntity;
import com.generated.platform.admin.entity.UserEntity;
import com.generated.platform.admin.mapper.RoleMapper;
import com.generated.platform.admin.mapper.UserMapper;
import com.generated.platform.common.BizException;
import com.generated.platform.common.ErrorCode;
import com.generated.platform.common.PageResult;
import com.generated.platform.security.PermVersionService;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;

import java.security.SecureRandom;
import java.util.List;
import java.util.Map;
import java.util.function.Function;
import java.util.stream.Collectors;

/**
 * 账号管理（契约 2.3 的 4 个端点）。
 *
 * <p>三条规则都在服务端：层级（不能碰同级或更高的人）、自身豁免（不能改自己）、
 * 改角色/停用/重置口令都要递增权限版本——否则降权在令牌过期前不生效。</p>
 */
@Service
public class UserService {

    private static final int MAX_PAGE_SIZE = 200;
    private static final String PASSWORD_ALPHABET =
            "ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz23456789@#$%";

    private final UserMapper userMapper;
    private final RoleMapper roleMapper;
    private final OperatorContext operatorContext;
    private final DelegationGuard delegationGuard;
    private final PermVersionService permVersionService;

    public UserService(UserMapper userMapper, RoleMapper roleMapper, OperatorContext operatorContext,
                       DelegationGuard delegationGuard, PermVersionService permVersionService) {
        this.userMapper = userMapper;
        this.roleMapper = roleMapper;
        this.operatorContext = operatorContext;
        this.delegationGuard = delegationGuard;
        this.permVersionService = permVersionService;
    }

    @Transactional(readOnly = true)
    public PageResult<AdminUserView> list(String keyword, String roleCode, Boolean enabled, Integer page, Integer size) {
        operatorContext.require("MANAGE_USER", "查看账号");

        var wrapper = Wrappers.<UserEntity>lambdaQuery();
        if (keyword != null && !keyword.isBlank()) {
            wrapper.like(UserEntity::getUsername, keyword.trim());
        }
        if (roleCode != null && !roleCode.isBlank()) {
            wrapper.eq(UserEntity::getRoleId, roleByCode(roleCode).getId());
        }
        if (enabled != null) {
            wrapper.eq(UserEntity::getEnabled, enabled ? 1 : 0);
        }

        long total = userMapper.selectCount(wrapper);
        int currentPage = (page == null || page < 1) ? 1 : page;
        int pageSize = (size == null || size < 1) ? 20 : Math.min(size, MAX_PAGE_SIZE);

        wrapper.orderByAsc(UserEntity::getId)
                .last("LIMIT " + pageSize + " OFFSET " + (long) (currentPage - 1) * pageSize);
        List<UserEntity> users = userMapper.selectList(wrapper);

        Map<Long, String> roleCodes = roleMapper.selectList(null).stream()
                .collect(Collectors.toMap(RoleEntity::getId, RoleEntity::getCode, (a, b) -> a));

        List<AdminUserView> rows = users.stream()
                .map(u -> toView(u, roleCodes.get(u.getRoleId())))
                .toList();
        return new PageResult<>(rows, total, currentPage, pageSize, total > (long) currentPage * pageSize);
    }

    @Transactional
    public AdminUserView create(CreateUserRequest request) {
        Operator operator = operatorContext.require("MANAGE_USER", "创建账号");
        RoleEntity role = roleByCode(request.roleCode());
        if (role.getLevel() >= operator.roleLevel()) {
            throw new BizException(ErrorCode.FORBIDDEN, "不能创建与自己同级或更高层级的账号");
        }
        Long exists = userMapper.selectCount(Wrappers.<UserEntity>lambdaQuery()
                .eq(UserEntity::getUsername, request.username().trim()));
        if (exists != null && exists > 0) {
            throw new BizException(ErrorCode.CONFLICT, "用户名已存在：" + request.username());
        }

        UserEntity user = new UserEntity();
        user.setUsername(request.username().trim());
        user.setPasswordHash(BCrypt.hashpw(request.password(), BCrypt.gensalt(10)));
        user.setRoleId(role.getId());
        user.setEnabled(1);
        user.setPermVersion(0);
        // 管理员设定的初始口令一律要求首登改密：口令经过第二个人手就不再是秘密
        user.setMustChangePassword(1);
        user.setCreatedBy(operator.userId());
        userMapper.insert(user);
        return toView(user, role.getCode());
    }

    @Transactional
    public void update(long id, UpdateUserRequest request) {
        Operator operator = operatorContext.require("MANAGE_USER", "修改账号");
        UserEntity target = requireUser(id);
        RoleEntity targetRole = roleMapper.selectById(target.getRoleId());
        delegationGuard.checkUserEditable(operator, target, targetRole);

        boolean changed = false;
        LambdaUpdateWrapper<UserEntity> update = Wrappers.<UserEntity>lambdaUpdate().eq(UserEntity::getId, id);

        if (request.roleCode() != null) {
            RoleEntity newRole = roleByCode(request.roleCode());
            if (newRole.getLevel() >= operator.roleLevel()) {
                throw new BizException(ErrorCode.FORBIDDEN, "不能把用户提升到自己同级或更高的角色");
            }
            update.set(UserEntity::getRoleId, newRole.getId());
            changed = true;
        }
        if (request.enabled() != null) {
            update.set(UserEntity::getEnabled, request.enabled() ? 1 : 0);
            changed = true;
        }
        if (!changed) {
            throw new BizException(ErrorCode.BAD_REQUEST, "至少要改 enabled 或 roleCode 其中一项");
        }

        userMapper.update(null, update);
        // 改角色、停用都要让旧令牌立刻失效
        permVersionService.bumpUser(id);
    }

    @Transactional
    public ResetPasswordResult resetPassword(long id) {
        Operator operator = operatorContext.require("MANAGE_USER", "重置口令");
        UserEntity target = requireUser(id);
        RoleEntity targetRole = roleMapper.selectById(target.getRoleId());
        delegationGuard.checkUserEditable(operator, target, targetRole);

        String initial = randomPassword();
        userMapper.update(null, Wrappers.<UserEntity>lambdaUpdate()
                .eq(UserEntity::getId, id)
                .set(UserEntity::getPasswordHash, BCrypt.hashpw(initial, BCrypt.gensalt(10)))
                .set(UserEntity::getMustChangePassword, 1));
        permVersionService.bumpUser(id);

        // 明文口令只在这一次响应里出现，服务端不存
        return new ResetPasswordResult(target.getUsername(), initial);
    }

    // ------------------------------------------------------------------ 内部

    private UserEntity requireUser(long id) {
        UserEntity user = userMapper.selectById(id);
        if (user == null) {
            throw new BizException(ErrorCode.NOT_FOUND, "用户不存在：" + id);
        }
        return user;
    }

    private RoleEntity roleByCode(String code) {
        RoleEntity role = roleMapper.selectOne(Wrappers.<RoleEntity>lambdaQuery().eq(RoleEntity::getCode, code));
        if (role == null) {
            throw new BizException(ErrorCode.NOT_FOUND, "角色不存在：" + code);
        }
        return role;
    }

    private AdminUserView toView(UserEntity user, String roleCode) {
        return new AdminUserView(user.getId(), user.getUsername(), roleCode,
                user.getEnabled() != null && user.getEnabled() == 1,
                user.getPermVersion() == null ? 0 : user.getPermVersion(),
                user.getMustChangePassword() != null && user.getMustChangePassword() == 1,
                user.getCreatedAt());
    }

    private String randomPassword() {
        SecureRandom random = new SecureRandom();
        StringBuilder builder = new StringBuilder(14);
        for (int i = 0; i < 14; i++) {
            builder.append(PASSWORD_ALPHABET.charAt(random.nextInt(PASSWORD_ALPHABET.length())));
        }
        return builder.toString();
    }
}
