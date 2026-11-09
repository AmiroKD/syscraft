package com.generated.platform.admin.entity;

import com.baomidou.mybatisplus.annotation.TableId;
import com.baomidou.mybatisplus.annotation.TableName;

import java.time.LocalDateTime;

/**
 * adm_request_dedup 映射：Agent 超时重试时凭 X-Request-Id 回放首次响应，
 * 避免重复插入（运行时 9.x 的幂等设计）。
 */
@TableName("adm_request_dedup")
public class RequestDedupEntity {

    @TableId
    private String requestId;

    /** 首次执行的响应原文，重试时原样回放 */
    private String responseJson;

    private LocalDateTime expiresAt;

    private LocalDateTime createdAt;

    public String getRequestId() {
        return requestId;
    }

    public void setRequestId(String requestId) {
        this.requestId = requestId;
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
