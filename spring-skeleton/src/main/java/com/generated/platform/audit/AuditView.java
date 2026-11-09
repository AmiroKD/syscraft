package com.generated.platform.audit;

/**
 * 审计查询结果。detailJson 原样透传（写入时已确认是合法 JSON），前端自行解析展示。
 */
public record AuditView(Long id, Long actorId, String action, String entity, String targetId,
                        Integer impactRows, String result, String detailJson, String traceId,
                        String createdAt) {
}
