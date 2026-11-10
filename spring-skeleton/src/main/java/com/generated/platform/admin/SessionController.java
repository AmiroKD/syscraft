package com.generated.platform.admin;

import com.generated.platform.admin.dto.AdminDtos.SessionView;
import com.generated.platform.admin.entity.SessionEntity;
import com.generated.platform.admin.mapper.SessionMapper;
import com.generated.platform.audit.AuditService;
import com.generated.platform.common.ApiResponse;
import com.generated.platform.security.SessionService;
import org.springframework.web.bind.annotation.DeleteMapping;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.PathVariable;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RestController;

import java.util.List;

/**
 * 在线会话管理接口（底座扩展 v0.1）。
 *
 * <p>在线名单对管理员开放（MANAGE_USER，运营视角：谁在用、从哪来）；
 * 强制下线仅超管（与停用账号同级的管理动作）。下线的执行是双保险：
 * 吊销标记按令牌哈希拦截 + perm_version 自增让令牌真正失效。</p>
 */
@RestController
@RequestMapping("/api/admin/sessions")
public class SessionController {

    private final SessionService sessionService;
    private final SessionMapper sessionMapper;
    private final OperatorContext operatorContext;
    private final AuditService auditService;

    public SessionController(SessionService sessionService, SessionMapper sessionMapper,
                             OperatorContext operatorContext, AuditService auditService) {
        this.sessionService = sessionService;
        this.sessionMapper = sessionMapper;
        this.operatorContext = operatorContext;
        this.auditService = auditService;
    }

    @GetMapping
    public ApiResponse<List<SessionView>> list() {
        operatorContext.require("MANAGE_USER", "查看在线会话");
        return ApiResponse.ok(sessionService.listActive().stream()
                .map(s -> new SessionView(s.id(), s.userId(), s.username(), s.ip(),
                        s.userAgent(), s.createdAt(), s.lastActiveAt()))
                .toList());
    }

    @DeleteMapping("/{id}")
    public ApiResponse<Void> forceLogout(@PathVariable long id) {
        operatorContext.requireSuperAdmin();
        long auditId = auditService.begin("FORCE_LOGOUT", "adm_session", String.valueOf(id));
        try {
            sessionService.forceLogout(id, auditActor());
            auditService.success(auditId, 1, "{\"revoked\":true}");
        } catch (RuntimeException e) {
            auditService.failed(auditId, e.getMessage());
            throw e;
        }
        return ApiResponse.ok();
    }

    private long auditActor() {
        return operatorContext.current().userId();
    }

    /** 供管理端查询单个会话详情（未导出，预留）；列表路径已覆盖演示需求 */
    @SuppressWarnings("unused")
    private SessionEntity load(long id) {
        return sessionMapper.selectById(id);
    }
}
