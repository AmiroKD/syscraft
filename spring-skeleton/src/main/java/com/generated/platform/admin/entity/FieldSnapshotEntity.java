package com.generated.platform.admin.entity;

import com.baomidou.mybatisplus.annotation.TableField;
import com.baomidou.mybatisplus.annotation.TableName;

/**
 * adm_field_snapshot 映射。复合主键 (entity, field)。
 *
 * <p>{@code sensitive} 是 MySQL 保留字，字段名沿用设计不改，但映射必须显式带反引号，
 * 否则 MyBatis-Plus 生成的 SQL 会 1064。</p>
 */
@TableName("adm_field_snapshot")
public class FieldSnapshotEntity {

    private String entity;

    private String field;

    private String dataType;

    private String semantic;

    private Integer nullable;

    private Integer uniqueFlag;

    @TableField("`sensitive`")
    private Integer sensitive;

    private String visibility;

    private Integer ordinal;

    public String getEntity() {
        return entity;
    }

    public void setEntity(String entity) {
        this.entity = entity;
    }

    public String getField() {
        return field;
    }

    public void setField(String field) {
        this.field = field;
    }

    public String getDataType() {
        return dataType;
    }

    public void setDataType(String dataType) {
        this.dataType = dataType;
    }

    public String getSemantic() {
        return semantic;
    }

    public void setSemantic(String semantic) {
        this.semantic = semantic;
    }

    public Integer getNullable() {
        return nullable;
    }

    public void setNullable(Integer nullable) {
        this.nullable = nullable;
    }

    public Integer getUniqueFlag() {
        return uniqueFlag;
    }

    public void setUniqueFlag(Integer uniqueFlag) {
        this.uniqueFlag = uniqueFlag;
    }

    public Integer getSensitive() {
        return sensitive;
    }

    public void setSensitive(Integer sensitive) {
        this.sensitive = sensitive;
    }

    public String getVisibility() {
        return visibility;
    }

    public void setVisibility(String visibility) {
        this.visibility = visibility;
    }

    public Integer getOrdinal() {
        return ordinal;
    }

    public void setOrdinal(Integer ordinal) {
        this.ordinal = ordinal;
    }
}
