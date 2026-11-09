package com.generated.platform.admin.entity;

import com.baomidou.mybatisplus.annotation.IdType;
import com.baomidou.mybatisplus.annotation.TableId;
import com.baomidou.mybatisplus.annotation.TableName;

import java.time.LocalDateTime;

/**
 * adm_audit 映射。两段式：先写 INTENT，执行完回填结果（接口清单 9.1）。
 * 中途崩溃会留下一条永远没有结果的 INTENT——这正是排查「那次操作到底做没做」的证据。
 * 没有删除接口。
 */
@TableName("adm_audit")
public class AuditEntity {

    @TableId(type = IdType.AUTO)
    private Long id;

    private Long actorId;

    private String action;

    private String entity;

    private String targetId;

    private Integer impactRows;

    /** INTENT / SUCCESS / FAILED */
    private String result;

    private String detailJson;

    /** 对应请求头 X-Agent-Trace，把一次对话的多条请求串成链路 */
    private String traceId;

    private LocalDateTime createdAt;

    private LocalDateTime updatedAt;

    public Long getId() {
        return id;
    }

    public void setId(Long id) {
        this.id = id;
    }

    public Long getActorId() {
        return actorId;
    }

    public void setActorId(Long actorId) {
        this.actorId = actorId;
    }

    public String getAction() {
        return action;
    }

    public void setAction(String action) {
        this.action = action;
    }

    public String getEntity() {
        return entity;
    }

    public void setEntity(String entity) {
        this.entity = entity;
    }

    public String getTargetId() {
        return targetId;
    }

    public void setTargetId(String targetId) {
        this.targetId = targetId;
    }

    public Integer getImpactRows() {
        return impactRows;
    }

    public void setImpactRows(Integer impactRows) {
        this.impactRows = impactRows;
    }

    public String getResult() {
        return result;
    }

    public void setResult(String result) {
        this.result = result;
    }

    public String getDetailJson() {
        return detailJson;
    }

    public void setDetailJson(String detailJson) {
        this.detailJson = detailJson;
    }

    public String getTraceId() {
        return traceId;
    }

    public void setTraceId(String traceId) {
        this.traceId = traceId;
    }

    public LocalDateTime getCreatedAt() {
        return createdAt;
    }

    public void setCreatedAt(LocalDateTime createdAt) {
        this.createdAt = createdAt;
    }

    public LocalDateTime getUpdatedAt() {
        return updatedAt;
    }

    public void setUpdatedAt(LocalDateTime updatedAt) {
        this.updatedAt = updatedAt;
    }
}
