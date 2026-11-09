package com.generated.platform.admin.entity;

import com.baomidou.mybatisplus.annotation.TableId;
import com.baomidou.mybatisplus.annotation.TableName;

import java.time.LocalDateTime;

/**
 * adm_entity_snapshot 映射。超管的权限码按这张表的全部实体展开（权限 4.6 配套第 1 条），
 * 所以「生成一张新表」必须显式刷新快照，超管才会自动获得它的权限——
 * 这条刻意不做成「超管拥有未来所有权限」的隐性放大。
 */
@TableName("adm_entity_snapshot")
public class EntitySnapshotEntity {

    @TableId
    private String entity;

    private String displayName;

    private String ownerField;

    private Integer fieldCount;

    private Long version;

    private LocalDateTime generatedAt;

    public String getEntity() {
        return entity;
    }

    public void setEntity(String entity) {
        this.entity = entity;
    }

    public String getDisplayName() {
        return displayName;
    }

    public void setDisplayName(String displayName) {
        this.displayName = displayName;
    }

    public String getOwnerField() {
        return ownerField;
    }

    public void setOwnerField(String ownerField) {
        this.ownerField = ownerField;
    }

    public Integer getFieldCount() {
        return fieldCount;
    }

    public void setFieldCount(Integer fieldCount) {
        this.fieldCount = fieldCount;
    }

    public Long getVersion() {
        return version;
    }

    public void setVersion(Long version) {
        this.version = version;
    }

    public LocalDateTime getGeneratedAt() {
        return generatedAt;
    }

    public void setGeneratedAt(LocalDateTime generatedAt) {
        this.generatedAt = generatedAt;
    }
}
