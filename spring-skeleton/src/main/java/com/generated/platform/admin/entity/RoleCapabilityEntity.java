package com.generated.platform.admin.entity;

import com.baomidou.mybatisplus.annotation.TableName;

/**
 * adm_role_capability 映射。复合主键 (role_id, capability)，所以不声明 @TableId。
 *
 * <p>capability 的取值是九种封闭枚举，库里用 ENUM 固化过一层；这里保留 String，
 * 是因为平台只需比对，不需要在 Java 侧再做一次枚举校验（越权值根本插不进去）。</p>
 */
@TableName("adm_role_capability")
public class RoleCapabilityEntity {

    private Long roleId;

    private String capability;

    private Long createdBy;

    public Long getRoleId() {
        return roleId;
    }

    public void setRoleId(Long roleId) {
        this.roleId = roleId;
    }

    public String getCapability() {
        return capability;
    }

    public void setCapability(String capability) {
        this.capability = capability;
    }

    public Long getCreatedBy() {
        return createdBy;
    }

    public void setCreatedBy(Long createdBy) {
        this.createdBy = createdBy;
    }
}
