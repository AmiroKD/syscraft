package com.generated.platform.audit;

import com.generated.platform.common.ApiResponse;
import com.generated.platform.common.PageResult;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RequestParam;
import org.springframework.web.bind.annotation.RestController;

/**
 * 审计查询（接口清单 4 节）。过滤条件全部走索引列，trace_id 支持把一次对话的链路捞出来。
 */
@RestController
@RequestMapping("/api/admin/audits")
public class AuditController {

    private final AuditQueryService auditQueryService;

    public AuditController(AuditQueryService auditQueryService) {
        this.auditQueryService = auditQueryService;
    }

    @GetMapping
    public ApiResponse<PageResult<AuditView>> list(
            @RequestParam(required = false) Long actorId,
            @RequestParam(required = false) String entity,
            @RequestParam(required = false) String action,
            @RequestParam(required = false) String result,
            @RequestParam(required = false) Integer page,
            @RequestParam(required = false) Integer size) {
        return ApiResponse.ok(auditQueryService.list(actorId, entity, action, result, page, size));
    }
}
