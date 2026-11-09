package com.generated.platform.admin.entity;

import com.baomidou.mybatisplus.annotation.TableId;
import com.baomidou.mybatisplus.annotation.TableName;

import java.time.LocalDateTime;

/**
 * adm_model_snapshot 映射：生成输入的模型原件，每版本一行（契约 2.4 的
 * GET /api/meta/model 的落点）。审计用途：什么需求生成了什么系统。
 */
@TableName("adm_model_snapshot")
public class ModelSnapshotEntity {

    @TableId
    private Long version;

    /** 模型 JSON 原文。列类型是 MySQL JSON，String 进出即可 */
    private String modelJson;

    private Long createdBy;

    private LocalDateTime createdAt;

    public Long getVersion() {
        return version;
    }

    public void setVersion(Long version) {
        this.version = version;
    }

    public String getModelJson() {
        return modelJson;
    }

    public void setModelJson(String modelJson) {
        this.modelJson = modelJson;
    }

    public Long getCreatedBy() {
        return createdBy;
    }

    public void setCreatedBy(Long createdBy) {
        this.createdBy = createdBy;
    }

    public LocalDateTime getCreatedAt() {
        return createdAt;
    }

    public void setCreatedAt(LocalDateTime createdAt) {
        this.createdAt = createdAt;
    }
}
