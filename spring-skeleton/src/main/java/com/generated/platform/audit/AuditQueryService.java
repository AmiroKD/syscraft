package com.generated.platform.audit;

import com.baomidou.mybatisplus.core.conditions.query.LambdaQueryWrapper;
import com.baomidou.mybatisplus.core.toolkit.Wrappers;
import com.generated.platform.admin.OperatorContext;
import com.generated.platform.admin.entity.AuditEntity;
import com.generated.platform.admin.mapper.AuditMapper;
import com.generated.platform.common.PageResult;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;

import java.util.List;

/**
 * 审计查询。只读，没有删除接口也没有修改接口——审计记录被改掉等于证据被改掉。
 */
@Service
public class AuditQueryService {

    private static final int MAX_PAGE_SIZE = 200;

    private final AuditMapper auditMapper;
    private final OperatorContext operatorContext;

    public AuditQueryService(AuditMapper auditMapper, OperatorContext operatorContext) {
        this.auditMapper = auditMapper;
        this.operatorContext = operatorContext;
    }

    @Transactional(readOnly = true)
    public PageResult<AuditView> list(Long actorId, String entity, String action, String result,
                                      Integer page, Integer size) {
        operatorContext.require("MANAGE_USER", "查看审计");

        LambdaQueryWrapper<AuditEntity> wrapper = Wrappers.<AuditEntity>lambdaQuery();
        if (actorId != null) {
            wrapper.eq(AuditEntity::getActorId, actorId);
        }
        if (entity != null && !entity.isBlank()) {
            wrapper.eq(AuditEntity::getEntity, entity.trim());
        }
        if (action != null && !action.isBlank()) {
            wrapper.eq(AuditEntity::getAction, action.trim());
        }
        if (result != null && !result.isBlank()) {
            wrapper.eq(AuditEntity::getResult, result.trim().toUpperCase());
        }
        wrapper.orderByDesc(AuditEntity::getId);

        long total = auditMapper.selectCount(wrapper);
        int currentPage = (page == null || page < 1) ? 1 : page;
        int pageSize = (size == null || size < 1) ? 20 : Math.min(size, MAX_PAGE_SIZE);
        wrapper.last("LIMIT " + pageSize + " OFFSET " + (long) (currentPage - 1) * pageSize);
        List<AuditEntity> rows = auditMapper.selectList(wrapper);

        List<AuditView> views = rows.stream()
                .map(row -> new AuditView(row.getId(), row.getActorId(), row.getAction(), row.getEntity(),
                        row.getTargetId(), row.getImpactRows(), row.getResult(), row.getDetailJson(),
                        row.getTraceId(), row.getCreatedAt() == null ? null : row.getCreatedAt().toString()))
                .toList();
        return new PageResult<>(views, total, currentPage, pageSize, total > (long) currentPage * pageSize);
    }
}
