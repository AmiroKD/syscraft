package com.generated.platform.admin;

import com.baomidou.mybatisplus.core.toolkit.Wrappers;
import com.generated.platform.admin.OperatorContext.Operator;
import com.generated.platform.admin.entity.FeatureRequestEntity;
import com.generated.platform.admin.mapper.FeatureRequestMapper;
import com.generated.platform.audit.AuditService;
import com.generated.platform.common.BizException;
import com.generated.platform.common.ErrorCode;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;

import java.time.LocalDateTime;
import java.util.List;
import java.util.Set;

/**
 * 需求池（底座扩展 v0.1，缺陷审查 P1-1 的产品化落点）。
 *
 * <p>对话兜底卡片在解释「系统没有这个能力」的同时上报一条需求，管理员在管理端
 * 看得到「谁、什么时候、想要什么」，采纳后触发生成、婉拒留痕——
 * 把「系统缺什么」从一句提示词变成可运营的数据。</p>
 *
 * <p>上报端点对所有登录用户开放（登记不是提权），处理端点仅超管
 * （采纳等于决定「下一个生成什么」，与生成同权，见 P0-2 的角色门槛）。</p>
 */
@Service
public class FeatureRequestService {

    /** 上报频控：同一用户 10 分钟内至多 20 条，防止兜底卡片被刷成垃圾池 */
    private static final int REPORT_LIMIT_PER_10MIN = 20;

    private static final Set<String> HANDLED_STATUSES = Set.of("ACCEPTED", "REJECTED", "DONE");

    private final FeatureRequestMapper requestMapper;
    private final OperatorContext operatorContext;
    private final AuditService auditService;

    public FeatureRequestService(FeatureRequestMapper requestMapper, OperatorContext operatorContext,
                                 AuditService auditService) {
        this.requestMapper = requestMapper;
        this.operatorContext = operatorContext;
        this.auditService = auditService;
    }

    @Transactional
    public long report(Long userId, String rawText, String wantedEntity, String source) {
        if (userId != null) {
            Long recent = requestMapper.selectCount(Wrappers.<FeatureRequestEntity>lambdaQuery()
                    .eq(FeatureRequestEntity::getUserId, userId)
                    .gt(FeatureRequestEntity::getCreatedAt, LocalDateTime.now().minusMinutes(10)));
            if (recent != null && recent >= REPORT_LIMIT_PER_10MIN) {
                throw new BizException(ErrorCode.CONFLICT, "需求上报太频繁，请稍后再试");
            }
        }

        FeatureRequestEntity row = new FeatureRequestEntity();
        row.setUserId(userId);
        row.setRawText(truncate(rawText, 500));
        row.setWantedEntity(truncate(wantedEntity, 64));
        row.setSource(truncate(source, 32) == null ? "CHAT_FALLBACK" : truncate(source, 32));
        row.setStatus("OPEN");
        requestMapper.insert(row);
        return row.getId();
    }

    @Transactional(readOnly = true)
    public List<FeatureRequestEntity> list(String status) {
        Operator operator = operatorContext.require("MANAGE_USER", "查看需求池");
        var wrapper = Wrappers.<FeatureRequestEntity>lambdaQuery()
                .orderByDesc(FeatureRequestEntity::getCreatedAt)
                .last("LIMIT 200");
        if (status != null && !status.isBlank()) {
            wrapper.eq(FeatureRequestEntity::getStatus, status.trim().toUpperCase());
        }
        return requestMapper.selectList(wrapper);
    }

    /** 采纳 / 婉拒 / 归档。婉拒必须带原因——留痕是为了下一个人不用重新猜一遍 */
    @Transactional
    public void handle(long id, String status, String note) {
        Operator operator = operatorContext.requireSuperAdmin();
        String normalized = status == null ? "" : status.trim().toUpperCase();
        if (!HANDLED_STATUSES.contains(normalized)) {
            throw new BizException(ErrorCode.BAD_REQUEST, "status 只能是 ACCEPTED / REJECTED / DONE");
        }
        if ("REJECTED".equals(normalized) && (note == null || note.isBlank())) {
            throw new BizException(ErrorCode.BAD_REQUEST, "婉拒必须写明原因");
        }
        FeatureRequestEntity row = requestMapper.selectById(id);
        if (row == null) {
            throw new BizException(ErrorCode.NOT_FOUND, "需求不存在：" + id);
        }

        long auditId = auditService.begin("FEATURE_HANDLE", "adm_feature_request", String.valueOf(id));
        try {
            requestMapper.update(null, Wrappers.<FeatureRequestEntity>lambdaUpdate()
                    .eq(FeatureRequestEntity::getId, id)
                    .set(FeatureRequestEntity::getStatus, normalized)
                    .set(FeatureRequestEntity::getHandledBy, operator.userId())
                    .set(FeatureRequestEntity::getHandledAt, LocalDateTime.now())
                    .set(FeatureRequestEntity::getNote, truncate(note, 500)));
            auditService.success(auditId, 1, "{\"status\":\"" + normalized + "\"}");
        } catch (RuntimeException e) {
            auditService.failed(auditId, e.getMessage());
            throw e;
        }
    }

    private String truncate(String value, int max) {
        if (value == null) {
            return null;
        }
        String trimmed = value.trim();
        return trimmed.length() > max ? trimmed.substring(0, max) : trimmed;
    }
}
