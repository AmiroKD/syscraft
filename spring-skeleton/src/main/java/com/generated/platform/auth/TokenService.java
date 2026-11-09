package com.generated.platform.auth;

import cn.dev33.satoken.stp.StpUtil;
import cn.dev33.satoken.stp.parameter.SaLoginParameter;
import org.springframework.stereotype.Component;

/**
 * 令牌签发与读取。
 *
 * <p>JWT Stateless：令牌自包含，服务端不存会话，重启不掉线（《技术栈》6 节：
 * 不引入 Redis 的代价就是「注销仅前端清令牌」）。</p>
 *
 * <p>令牌里只放两样东西：用户 id 与<b>权限版本号</b>，不放角色与权限快照。
 * 角色随时可被管理员调整，写进令牌就意味着降权在令牌过期前不生效；
 * 带上版本号则服务端比对不一致即判定失效，无状态与「变更即时生效」才能同时成立。</p>
 */
@Component
public class TokenService {

    /** 令牌载荷里的权限版本键名 */
    private static final String CLAIM_PERM_VERSION = "pv";

    public String issue(Long userId, int permVersion) {
        SaLoginParameter model = new SaLoginParameter().setExtra(CLAIM_PERM_VERSION, permVersion);
        StpUtil.login(userId, model);
        return StpUtil.getTokenValue();
    }

    public long currentUserId() {
        return StpUtil.getLoginIdAsLong();
    }

    /** 令牌所载权限版本；老令牌没带这个键时返回 null，调用方一律按「需重登」处理 */
    public Integer currentPermVersion() {
        Object pv = StpUtil.getExtra(CLAIM_PERM_VERSION);
        return pv instanceof Number n ? n.intValue() : null;
    }
}
