package com.generated.platform.security;

import cn.dev33.satoken.stp.StpUtil;
import com.generated.platform.auth.TokenService;
import com.generated.platform.common.BizException;
import com.generated.platform.common.ErrorCode;
import org.springframework.stereotype.Component;

/**
 * 每个受保护请求都比对权限版本（权限 2 节 / 4.9）。
 *
 * <p>没有这一步，令牌自包含就变成了「降权要等到过期」——管理员刚把某人降级，
 * 他手里的令牌还能用 12 小时。版本号比对把「无状态」与「变更即时生效」接上。</p>
 *
 * <p>放在拦截器而不是各 Service：漏一处就是一个越权入口，而这属于入口级判断。</p>
 */
@Component
public class PermVersionGuard {

    private final TokenService tokenService;
    private final PermVersionService permVersionService;

    public PermVersionGuard(TokenService tokenService, PermVersionService permVersionService) {
        this.tokenService = tokenService;
        this.permVersionService = permVersionService;
    }

    public void check() {
        check(StpUtil.getLoginIdAsLong());
    }

    /** 供回放路径复用：回放不经认证拦截器，身份与版本在这里补比 */
    public void check(long userId) {
        Integer tokenVersion = tokenService.currentPermVersion();
        int currentVersion = permVersionService.current(userId);
        if (tokenVersion == null || tokenVersion != currentVersion) {
            throw new BizException(ErrorCode.TOKEN_STALE, "权限已变更，请重新登录");
        }
    }
}
