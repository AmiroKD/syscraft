package com.generated.platform.admin;

import com.generated.platform.admin.OperatorContext.Operator;
import com.generated.platform.admin.entity.RoleEntity;
import com.generated.platform.admin.entity.UserEntity;
import com.generated.platform.common.BizException;
import com.generated.platform.common.ErrorCode;
import org.springframework.stereotype.Component;

import java.util.Set;

/**
 * 五条委托约束的代码化（权限 4.2 / 接口清单 3 节）。
 *
 * <pre>
 * checkRoleEditable:  level(op.role) > level(target)  && target.code != op.roleCode
 * checkGrantable:     caps ⊆ capabilities(op.role)     && !caps.contains(CREATE_TABLE|ALTER_TABLE)
 * checkUserEditable:  level(op.role) > level(target.role) && target.id != op.id
 * checkRoleCreatable: newLevel < level(op.role)
 * </pre>
 *
 * <p>这些是<b>服务端</b>约束，不是提示词约束：Agent 先判过一遍也只算体验优化，
 * 真正拦住提权的是这里。</p>
 */
@Component
public class DelegationGuard {

    /**
     * 建表/改表不能委托。硬编码而不是查表——它不该被任何人从配置里改掉；
     * 改了就等于把「生成仅超管」这条约束变成可配置项（接口清单 3 节）。
     */
    private static final Set<String> SUPER_ADMIN_ONLY = Set.of("CREATE_TABLE", "ALTER_TABLE");

    public void checkRoleEditable(Operator operator, RoleEntity target) {
        if (target.getLevel() == null || operator.roleLevel() <= target.getLevel()) {
            throw new BizException(ErrorCode.FORBIDDEN, "只能修改层级低于自己的角色");
        }
        // 自身豁免：改自己所属的角色 = 给自己抬权限
        if (target.getCode() != null && target.getCode().equals(operator.roleCode())) {
            throw new BizException(ErrorCode.FORBIDDEN, "不能修改自己所属的角色");
        }
    }

    public void checkGrantable(Operator operator, Set<String> capabilities) {
        if (capabilities == null) {
            return;
        }
        for (String capability : capabilities) {
            if (SUPER_ADMIN_ONLY.contains(capability)) {
                throw new BizException(ErrorCode.FORBIDDEN, capability + " 为超管专有，不可授予任何角色");
            }
            if (!operator.capabilities().contains(capability)) {
                throw new BizException(ErrorCode.FORBIDDEN, "不能授予自己没有的能力：" + capability);
            }
        }
    }

    public void checkUserEditable(Operator operator, UserEntity target, RoleEntity targetRole) {
        if (target.getId() != null && target.getId() == operator.userId()) {
            throw new BizException(ErrorCode.FORBIDDEN, "不能修改自己的账号");
        }
        if (targetRole == null || targetRole.getLevel() == null || operator.roleLevel() <= targetRole.getLevel()) {
            throw new BizException(ErrorCode.FORBIDDEN, "只能操作层级低于自己的用户");
        }
    }

    public void checkRoleCreatable(Operator operator, int level) {
        if (level >= operator.roleLevel()) {
            throw new BizException(ErrorCode.FORBIDDEN, "新角色的层级必须低于自己");
        }
    }
}
