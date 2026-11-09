package com.generated.platform.admin.entity;

import com.baomidou.mybatisplus.annotation.TableId;
import com.baomidou.mybatisplus.annotation.TableName;

import java.time.LocalDateTime;

/**
 * adm_request_dedup 映射：Agent 超时重试时凭 X-Request-Id 回放首次响应，
 * 避免重复插入（运行时 9.x 的幂等设计）。
 *
 * <p>responseJson 为 null 表示「占坑未完成」：请求已进业务链但响应还没落，
 * 并发同键的请求见到 null 要拒绝（409）而不是重复执行。</p>
 */
@TableName("adm_request_dedup")
public class RequestDedupEntity {

    @TableId
    private String requestId;

    /** 首次请求的用户；回放只对同一用户生效（不能拿别人的响应当自己的） */
    private Long userId;

    /** 首次执行的响应原文；null = 占坑中 */
    private String responseJson;

    private LocalDateTime expiresAt;

    private LocalDateTime createdAt;

    public String getRequestId() {
        return requestId;
    }

    public void setRequestId(String requestId) {
        this.requestId = requestId;
    }

    public Long getUserId() {
        return userId;
    }

    public void setUserId(Long userId) {
        this.userId = userId;
    }

    public String getResponseJson() {
        return responseJson;
    }

    public void setResponseJson(String responseJson) {
        this.responseJson = responseJson;
    }

    public LocalDateTime getExpiresAt() {
        return expiresAt;
    }

    public void setExpiresAt(LocalDateTime expiresAt) {
        this.expiresAt = expiresAt;
    }

    public LocalDateTime getCreatedAt() {
        return createdAt;
    }

    public void setCreatedAt(LocalDateTime createdAt) {
        this.createdAt = createdAt;
    }
}
