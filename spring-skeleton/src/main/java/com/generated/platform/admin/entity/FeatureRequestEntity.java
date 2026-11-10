package com.generated.platform.admin.entity;

import com.baomidou.mybatisplus.annotation.IdType;
import com.baomidou.mybatisplus.annotation.TableId;
import com.baomidou.mybatisplus.annotation.TableName;

import java.time.LocalDateTime;

/**
 * adm_feature_request 映射（底座扩展 v0.1）。
 *
 * <p>需求池一行 = 用户要了而系统还没有的一个能力。来源以对话兜底为主：
 * 模型调用了不存在的实体时，兜底卡片在解释能力边界的同时上报一条，
 * 让「系统缺什么」从口头禅变成可运营的数据。</p>
 */
@TableName("adm_feature_request")
public class FeatureRequestEntity {

    @TableId(type = IdType.AUTO)
    private Long id;

    private Long userId;

    /** 需求原话，超长截断 */
    private String rawText;

    /** 对话中模型试图调用的实体名，可空 */
    private String wantedEntity;

    /** CHAT_FALLBACK / ADMIN / OTHER */
    private String source;

    /** OPEN / ACCEPTED / REJECTED / DONE */
    private String status;

    private Long handledBy;

    private LocalDateTime handledAt;

    private String note;

    private LocalDateTime createdAt;

    public Long getId() {
        return id;
    }

    public void setId(Long id) {
        this.id = id;
    }

    public Long getUserId() {
        return userId;
    }

    public void setUserId(Long userId) {
        this.userId = userId;
    }

    public String getRawText() {
        return rawText;
    }

    public void setRawText(String rawText) {
        this.rawText = rawText;
    }

    public String getWantedEntity() {
        return wantedEntity;
    }

    public void setWantedEntity(String wantedEntity) {
        this.wantedEntity = wantedEntity;
    }

    public String getSource() {
        return source;
    }

    public void setSource(String source) {
        this.source = source;
    }

    public String getStatus() {
        return status;
    }

    public void setStatus(String status) {
        this.status = status;
    }

    public Long getHandledBy() {
        return handledBy;
    }

    public void setHandledBy(Long handledBy) {
        this.handledBy = handledBy;
    }

    public LocalDateTime getHandledAt() {
        return handledAt;
    }

    public void setHandledAt(LocalDateTime handledAt) {
        this.handledAt = handledAt;
    }

    public String getNote() {
        return note;
    }

    public void setNote(String note) {
        this.note = note;
    }

    public LocalDateTime getCreatedAt() {
        return createdAt;
    }

    public void setCreatedAt(LocalDateTime createdAt) {
        this.createdAt = createdAt;
    }
}
