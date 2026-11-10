package com.generated.platform.admin.entity;

import com.baomidou.mybatisplus.annotation.IdType;
import com.baomidou.mybatisplus.annotation.TableId;
import com.baomidou.mybatisplus.annotation.TableName;

import java.time.LocalDateTime;

/**
 * adm_login_attempt 映射（底座扩展 v0.1）。
 *
 * <p>每次登录尝试一行，成功与失败都记。与 {@link UserEntity} 的
 * failed_attempts/locked_until 配合：计数在账号行上，取证在这里。</p>
 */
@TableName("adm_login_attempt")
public class LoginAttemptEntity {

    @TableId(type = IdType.AUTO)
    private Long id;

    /** 尝试的登录名；不存在的用户名也记（防枚举取证） */
    private String username;

    /** 命中的用户；用户名不存在时为空 */
    private Long userId;

    /** 1=登录成功 */
    private Integer success;

    private String failReason;

    private String ip;

    private String userAgent;

    private LocalDateTime createdAt;

    public Long getId() {
        return id;
    }

    public void setId(Long id) {
        this.id = id;
    }

    public String getUsername() {
        return username;
    }

    public void setUsername(String username) {
        this.username = username;
    }

    public Long getUserId() {
        return userId;
    }

    public void setUserId(Long userId) {
        this.userId = userId;
    }

    public Integer getSuccess() {
        return success;
    }

    public void setSuccess(Integer success) {
        this.success = success;
    }

    public String getFailReason() {
        return failReason;
    }

    public void setFailReason(String failReason) {
        this.failReason = failReason;
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
}
