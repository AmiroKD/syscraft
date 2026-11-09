package com.generated.platform.audit;

import com.baomidou.mybatisplus.core.toolkit.Wrappers;
import com.generated.platform.admin.entity.AuditEntity;
import com.generated.platform.admin.mapper.AuditMapper;
import cn.dev33.satoken.stp.StpUtil;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.stereotype.Service;
import org.springframework.web.context.request.RequestContextHolder;
import org.springframework.web.context.request.ServletRequestAttributes;

/**
 * 审计两段式（接口清单 9.1）：执行前 INSERT result=INTENT，执行后回填 SUCCESS/FAILED 与影响行数。
 *
 * <p>为什么两段而不是一段：执行中途崩溃，一段式什么都没留下；两段式留下一条永远
 * 没有结果的 INTENT——这正是排查「那次操作到底做没做」的证据。</p>
 *
 * <p>trace_id 取请求头 X-Agent-Trace：一次对话会串起多条请求，靠它把链路连起来。
 * 从 RequestContextHolder 取而不是层层传参，生成的 Service 才不必关心请求头。</p>
 */
@Service
public class AuditService {

    private static final Logger log = LoggerFactory.getLogger(AuditService.class);

    private final AuditMapper auditMapper;

    public AuditService(AuditMapper auditMapper) {
        this.auditMapper = auditMapper;
    }

    public long begin(String action, String entity, String targetId) {
        AuditEntity row = new AuditEntity();
        row.setActorId(StpUtil.getLoginIdAsLong());
        row.setAction(action);
        row.setEntity(entity);
        row.setTargetId(targetId);
        row.setResult("INTENT");
        row.setTraceId(traceId());
        auditMapper.insert(row);
        return row.getId();
    }

    public void success(long auditId, int impactRows, String detailJson) {
        update(auditId, "SUCCESS", impactRows, detailJson);
    }

    public void failed(long auditId, String detailJson) {
        update(auditId, "FAILED", null, detailJson);
    }

    private void update(long auditId, String result, Integer impactRows, String detailJson) {
        try {
            auditMapper.update(null, Wrappers.<AuditEntity>lambdaUpdate()
                    .eq(AuditEntity::getId, auditId)
                    .set(AuditEntity::getResult, result)
                    .set(AuditEntity::getImpactRows, impactRows)
                    .set(AuditEntity::getDetailJson, detailJson));
        } catch (RuntimeException e) {
            // 审计写不进去不能连累业务回滚决策，但要大声记日志
            log.error("审计回填失败 auditId={} result={}", auditId, result, e);
        }
    }

    private String traceId() {
        if (RequestContextHolder.getRequestAttributes() instanceof ServletRequestAttributes attributes) {
            String trace = attributes.getRequest().getHeader("X-Agent-Trace");
            if (trace != null && !trace.isBlank()) {
                return trace.length() > 64 ? trace.substring(0, 64) : trace;
            }
        }
        return null;
    }
}
