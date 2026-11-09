package com.generated.platform.meta;

import org.apache.ibatis.annotations.Param;
import org.apache.ibatis.annotations.Select;

import java.util.List;

/**
 * 能力视图的只读查询。独立成接口而不是塞进某个业务 Mapper：
 * 它横跨授权表与列策略表，两边都不是它的「主表」。
 */
public interface GrantViewMapper {

    record GrantRow(String entity, String access) {
    }

    record PolicyRow(String entity, String field, String visibility) {
    }

    /** 用户级 + 角色级的全部授权行 */
    @Select("SELECT entity, access FROM adm_table_grant "
            + "WHERE (subject_type = 'USER' AND subject_id = #{userId}) "
            + "   OR (subject_type = 'ROLE' AND subject_id = #{roleId})")
    List<GrantRow> rowsOf(@Param("userId") long userId, @Param("roleId") long roleId);

    /** 全局隐藏字段（列策略实体级，不分角色） */
    @Select("SELECT entity, field, visibility FROM adm_column_policy WHERE visibility = 'HIDDEN'")
    List<PolicyRow> hiddenFields();
}
