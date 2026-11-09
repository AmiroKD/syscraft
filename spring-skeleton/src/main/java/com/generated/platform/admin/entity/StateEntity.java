package com.generated.platform.admin.entity;

import com.baomidou.mybatisplus.annotation.TableId;
import com.baomidou.mybatisplus.annotation.TableName;

import java.time.LocalDateTime;

/**
 * adm_state 映射。单行表（id 恒为 1，库里有 CHECK 兜底）。
 *
 * <p>initialized 的语义（基线 B1）：完成首次生成（建了业务表）才算 1，
 * 与「超管是否改过口令」无关——首次引导只看这一个字段。</p>
 */
@TableName("adm_state")
public class StateEntity {

    @TableId
    private Integer id;

    private Integer initialized;

    private String currentVersion;

    private Long snapshotVersion;

    private Long modelVersion;

    private LocalDateTime updatedAt;

    public Integer getId() {
        return id;
    }

    public void setId(Integer id) {
        this.id = id;
    }

    public Integer getInitialized() {
        return initialized;
    }

    public void setInitialized(Integer initialized) {
        this.initialized = initialized;
    }

    public String getCurrentVersion() {
        return currentVersion;
    }

    public void setCurrentVersion(String currentVersion) {
        this.currentVersion = currentVersion;
    }

    public Long getSnapshotVersion() {
        return snapshotVersion;
    }

    public void setSnapshotVersion(Long snapshotVersion) {
        this.snapshotVersion = snapshotVersion;
    }

    public Long getModelVersion() {
        return modelVersion;
    }

    public void setModelVersion(Long modelVersion) {
        this.modelVersion = modelVersion;
    }

    public LocalDateTime getUpdatedAt() {
        return updatedAt;
    }

    public void setUpdatedAt(LocalDateTime updatedAt) {
        this.updatedAt = updatedAt;
    }
}
