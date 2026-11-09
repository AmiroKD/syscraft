package com.generated.platform.admin;

import com.baomidou.mybatisplus.core.toolkit.Wrappers;
import com.generated.platform.admin.OperatorContext.Operator;
import com.generated.platform.admin.dto.AdminDtos.GrantRequest;
import com.generated.platform.admin.dto.AdminDtos.GrantView;
import com.generated.platform.admin.entity.RoleEntity;
import com.generated.platform.admin.entity.TableGrantEntity;
import com.generated.platform.admin.entity.UserEntity;
import com.generated.platform.admin.mapper.EntitySnapshotMapper;
import com.generated.platform.admin.mapper.RoleMapper;
import com.generated.platform.admin.mapper.TableGrantMapper;
import com.generated.platform.admin.mapper.UserMapper;
import com.generated.platform.common.BizException;
import com.generated.platform.common.ErrorCode;
import com.generated.platform.security.PermVersionService;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;

import java.util.List;
import java.util.Set;

/**
 * 表级授权（契约 2.3 的 3 个端点）。
 *
 * <p>三条硬规则：<b>一次只对一个主体一张表</b>（批量授权会让「给所有人开所有表」变成一句话）、
 * <b>只能授层级低于自己的主体</b>、<b>只能给快照里存在的表授权</b>——
 * 后者防的是「指向已删表的授权在重建同名表时静默生效」，那是漏洞不是体验问题（运行时 9.3）。</p>
 */
@Service
public class GrantService {

    private static final Set<String> ACCESS_LEVELS = Set.of("READ", "READ_WRITE", "FULL");
    private static final Set<String> SUBJECT_TYPES = Set.of("USER", "ROLE");

    private final TableGrantMapper grantMapper;
    private final UserMapper userMapper;
    private final RoleMapper roleMapper;
    private final EntitySnapshotMapper entitySnapshotMapper;
    private final OperatorContext operatorContext;
    private final PermVersionService permVersionService;

    public GrantService(TableGrantMapper grantMapper, UserMapper userMapper, RoleMapper roleMapper,
                        EntitySnapshotMapper entitySnapshotMapper, OperatorContext operatorContext,
                        PermVersionService permVersionService) {
        this.grantMapper = grantMapper;
        this.userMapper = userMapper;
        this.roleMapper = roleMapper;
        this.entitySnapshotMapper = entitySnapshotMapper;
        this.operatorContext = operatorContext;
        this.permVersionService = permVersionService;
    }

    @Transactional(readOnly = true)
    public List<GrantView> list(String subjectType, Long subjectId) {
        Operator operator = operatorContext.require("GRANT", "查看授权");

        var wrapper = Wrappers.<TableGrantEntity>lambdaQuery();
        if (subjectType != null && !subjectType.isBlank()) {
            wrapper.eq(TableGrantEntity::getSubjectType, subjectType.trim().toUpperCase());
        }
        if (subjectId != null) {
            wrapper.eq(TableGrantEntity::getSubjectId, subjectId);
        }
        wrapper.orderByAsc(TableGrantEntity::getEntity);

        return grantMapper.selectList(wrapper).stream()
                .filter(grant -> visible(operator, grant))
                .map(this::toView)
                .toList();
    }

    @Transactional
    public GrantView grant(GrantRequest request) {
        Operator operator = operatorContext.require("GRANT", "授权");

        String subjectType = request.subjectType().trim().toUpperCase();
        String access = request.access().trim().toUpperCase();
        String entity = request.entity().trim();
        if (!SUBJECT_TYPES.contains(subjectType)) {
            throw new BizException(ErrorCode.BAD_REQUEST, "subjectType 只能是 USER 或 ROLE");
        }
        if (!ACCESS_LEVELS.contains(access)) {
            throw new BizException(ErrorCode.BAD_REQUEST, "access 只能是 READ / READ_WRITE / FULL");
        }
        if (entitySnapshotMapper.selectById(entity) == null) {
            throw new BizException(ErrorCode.NOT_FOUND, "结构快照里没有这张表：" + entity);
        }
        requireSubjectBelowOperator(operator, subjectType, request.subjectId());

        TableGrantEntity existing = grantMapper.selectOne(Wrappers.<TableGrantEntity>lambdaQuery()
                .eq(TableGrantEntity::getSubjectType, subjectType)
                .eq(TableGrantEntity::getSubjectId, request.subjectId())
                .eq(TableGrantEntity::getEntity, entity));

        if (existing == null) {
            existing = new TableGrantEntity();
            existing.setSubjectType(subjectType);
            existing.setSubjectId(request.subjectId());
            existing.setEntity(entity);
            existing.setAccess(access);
            existing.setGrantedBy(operator.userId());
            grantMapper.insert(existing);
        } else {
            existing.setAccess(access);
            existing.setGrantedBy(operator.userId());
            grantMapper.updateById(existing);
        }

        permVersionService.bumpSubject(subjectType, request.subjectId());
        return toView(existing);
    }

    @Transactional
    public void revoke(long id) {
        Operator operator = operatorContext.require("GRANT", "撤销授权");
        TableGrantEntity grant = grantMapper.selectById(id);
        if (grant == null) {
            throw new BizException(ErrorCode.NOT_FOUND, "授权不存在：" + id);
        }
        if (!visible(operator, grant)) {
            throw new BizException(ErrorCode.FORBIDDEN, "无权撤销这条授权");
        }
        grantMapper.deleteById(id);
        permVersionService.bumpSubject(grant.getSubjectType(), grant.getSubjectId());
    }

    // ------------------------------------------------------------------ 内部

    /**
     * 可见范围：超管看全量；其他人只看见自己授出的、以及层级低于自己的主体的授权
     * （权限 4.4：可见范围与委托约束一致）。
     */
    private boolean visible(Operator operator, TableGrantEntity grant) {
        if (operator.isSuperAdmin()) {
            return true;
        }
        if (grant.getGrantedBy() != null && grant.getGrantedBy() == operator.userId()) {
            return true;
        }
        RoleEntity subjectRole = roleOfSubject(grant.getSubjectType(), grant.getSubjectId());
        return subjectRole != null && subjectRole.getLevel() != null && subjectRole.getLevel() < operator.roleLevel();
    }

    private void requireSubjectBelowOperator(Operator operator, String subjectType, Long subjectId) {
        RoleEntity role = roleOfSubject(subjectType, subjectId);
        if (role == null) {
            throw new BizException(ErrorCode.NOT_FOUND, "授权主体不存在：" + subjectType + "#" + subjectId);
        }
        if (role.getLevel() == null || role.getLevel() >= operator.roleLevel()) {
            throw new BizException(ErrorCode.FORBIDDEN, "只能给层级低于自己的主体授权");
        }
    }

    private RoleEntity roleOfSubject(String subjectType, Long subjectId) {
        if ("ROLE".equals(subjectType)) {
            return roleMapper.selectById(subjectId);
        }
        UserEntity user = userMapper.selectById(subjectId);
        return user == null ? null : roleMapper.selectById(user.getRoleId());
    }

    private GrantView toView(TableGrantEntity grant) {
        return new GrantView(grant.getId(), grant.getSubjectType(), grant.getSubjectId(),
                grant.getEntity(), grant.getAccess(), grant.getGrantedBy());
    }
}
