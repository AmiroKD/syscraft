package com.generated.platform.admin.entity;

import com.baomidou.mybatisplus.annotation.IdType;
import com.baomidou.mybatisplus.annotation.TableId;
import com.baomidou.mybatisplus.annotation.TableName;

import java.time.LocalDateTime;

/**
 * adm_session 映射（底座扩展 v0.1）。
 *
 * <p>JWT 本身保持无状态，本表是登记簿不是会话存储：在线可见、强制下线的
 * 触发标记、活跃度信号。真正的令牌失效开关仍是 perm_version 比对——
 * 吊销会话时两者同时做，任何一条命中级拦下请求。</p>
 */
@TableName("adm_session")
public class SessionEntity {

    @TableId(type = IdType.AUTO)
    private Long id;

    private Long userId;

    /** 令牌的 SHA-256 十六进制；明文不入库 */
    private String tokenHash;

    private String ip;

    private String userAgent;

    private LocalDateTime createdAt;

    private LocalDateTime lastActiveAt;

    /** 1=已吊销（管理员强制下线） */
    private Integer revoked;

    private LocalDateTime revokedAt;

    private Long revokedBy;

    private String revokedReason;

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

    public String getTokenHash() {
        return tokenHash;
    }

    public void setTokenHash(String tokenHash) {
        this.tokenHash = tokenHash;
    }

    public String getIp() {
        return ip;
    }

    public void setIp(String ip) {
        this.ip = ip;
    }

    public String getUserAgent() {
        return userAgent;
    }

    public void setUserAgent(String userAgent) {
        this.userAgent = userAgent;
    }

    public LocalDateTime getCreatedAt() {
        return createdAt;
    }

    public void setCreatedAt(LocalDateTime createdAt) {
        this.createdAt = createdAt;
    }

    public LocalDateTime getLastActiveAt() {
        return lastActiveAt;
    }

    public void setLastActiveAt(LocalDateTime lastActiveAt) {
        this.lastActiveAt = lastActiveAt;
    }

    public Integer getRevoked() {
        return revoked;
    }

    public void setRevoked(Integer revoked) {
        this.revoked = revoked;
    }

    public LocalDateTime getRevokedAt() {
        return revokedAt;
    }

    public void setRevokedAt(LocalDateTime revokedAt) {
        this.revokedAt = revokedAt;
    }

    public Long getRevokedBy() {
        return revokedBy;
    }

    public void setRevokedBy(Long revokedBy) {
        this.revokedBy = revokedBy;
    }

    public String getRevokedReason() {
        return revokedReason;
    }

    public void setRevokedReason(String revokedReason) {
        this.revokedReason = revokedReason;
    }
}
