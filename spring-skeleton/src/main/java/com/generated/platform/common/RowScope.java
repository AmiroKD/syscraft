package com.generated.platform.common;

import cn.dev33.satoken.stp.StpUtil;
import com.generated.platform.admin.OperatorContext;
import com.generated.platform.common.BizException;
import com.generated.platform.common.ErrorCode;
import org.springframework.stereotype.Component;

/**
 * 数据行级权限（三层防线的最后一段）：表级靠权限码、字段级靠 ColumnPolicyAdvice、
 * 行级靠这里。语义（adm_entity_snapshot.owner_field）：
 * owner 字段为空 → 该表不做行级隔离；非空 → 非超管只能看见/操作「归属是自己」的行。
 *
 * <p>为什么在模板层而不是 MyBatis 拦截器：解析 SQL 找表名再改写，复杂度高且
 * 对 LambdaQueryWrapper 生态兼容性差；模板里实体和字段都是静态已知的，
 * 一个条件调用就把过滤做在明处——生成代码可读、可审计，没有隐藏改写。</p>
 *
 * <p>实例组件：判定要走 OperatorContext（拿角色），而它是请求态的 Bean；
 * 生成的 Service 注入本组件后调用实例方法。</p>
 */
@Component
public class RowScope {

    private final OperatorContext operatorContext;

    public RowScope(OperatorContext operatorContext) {
        this.operatorContext = operatorContext;
    }

    /**
     * 行级过滤值：null = 不加过滤（超管豁免）。生成的查询/聚合据此决定要不要 eq。
     */
    public Long ownerId() {
        var operator = operatorContext.current();
        return operator.isSuperAdmin() ? null : operator.userId();
    }

    /** insert 时写入归属：数据是谁的，创建那一刻就定死，不依赖后续维护 */
    public long currentUserId() {
        return StpUtil.getLoginIdAsLong();
    }

    /** update/delete 前校验：非超管改不动别人的行 */
    public void checkOwner(Long rowOwner) {
        var operator = operatorContext.current();
        if (operator.isSuperAdmin()) {
            return;
        }
        if (rowOwner == null || rowOwner != operator.userId()) {
            throw new BizException(ErrorCode.FORBIDDEN, "该记录不属于你，无权操作");
        }
    }
}
