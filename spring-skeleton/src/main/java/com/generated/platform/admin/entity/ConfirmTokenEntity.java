package com.generated.platform.admin.entity;

import com.baomidou.mybatisplus.annotation.TableId;
import com.baomidou.mybatisplus.annotation.TableName;

import java.time.LocalDateTime;

/**
 * adm_confirm_token 映射。只存令牌哈希，明文令牌只在预告响应里出现一次。
 *
 * <p>三态机 ISSUED → CLAIMED → CONSUMED，业务失败回退 ISSUED 可重试（接口清单 5）。</p>
 */
@TableName("adm_confirm_token")
public class ConfirmTokenEntity {

    /** 明文令牌的 SHA-256，主键 */
    @TableId
    private String tokenHash;

    /** 操作 + 实体 + 规范化参数 + 影响行数的哈希，防「预告 A 执行 B」 */
    private String fingerprint;

    /** DELETE / BATCH_INSERT / UPDATE */
    private String opType;

    private String entity;

    private Integer impactRows;

    private Long issuedBy;

    private LocalDateTime expiresAt;

    /** ISSUED / CLAIMED / CONSUMED */
    private String state;

    private LocalDateTime claimedAt;

    private LocalDateTime usedAt;

    private LocalDateTime createdAt;

    public String getTokenHash() {
        return tokenHash;
    }

    public void setTokenHash(String tokenHash) {
        this.tokenHash = tokenHash;
    }

    public String getFingerprint() {
        return fingerprint;
    }

    public void setFingerprint(String fingerprint) {
        this.fingerprint = fingerprint;
    }

    public String getOpType() {
        return opType;
    }

    public void setOpType(String opType) {
        this.opType = opType;
    }

    public String getEntity() {
        return entity;
    }

    public void setEntity(String entity) {
        this.entity = entity;
    }

    public Integer getImpactRows() {
        return impactRows;
    }

    public void setImpactRows(Integer impactRows) {
        this.impactRows = impactRows;
    }

    public Long getIssuedBy() {
        return issuedBy;
    }

    public void setIssuedBy(Long issuedBy) {
        this.issuedBy = issuedBy;
    }

    public LocalDateTime getExpiresAt() {
        return expiresAt;
    }

    public void setExpiresAt(LocalDateTime expiresAt) {
        this.expiresAt = expiresAt;
    }

    public String getState() {
        return state;
    }

    public void setState(String state) {
        this.state = state;
    }

    public LocalDateTime getClaimedAt() {
        return claimedAt;
    }

    public void setClaimedAt(LocalDateTime claimedAt) {
        this.claimedAt = claimedAt;
    }

    public LocalDateTime getUsedAt() {
        return usedAt;
    }

    public void setUsedAt(LocalDateTime usedAt) {
        this.usedAt = usedAt;
    }

    public LocalDateTime getCreatedAt() {
        return createdAt;
    }

    public void setCreatedAt(LocalDateTime createdAt) {
        this.createdAt = createdAt;
    }
}
