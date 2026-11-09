package com.generated.platform.meta;

import com.generated.platform.admin.OperatorContext;
import com.generated.platform.common.ApiResponse;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RestController;

/**
 * 结构快照查询（契约 2.4）。
 *
 * <p>全量快照含所有表与字段，只给超管：生成模式只有超管有入口，
 * 而这份快照正是 Agent 在生成模式下认知系统的输入（权限 3 节）。</p>
 */
@RestController
@RequestMapping("/api/meta")
public class MetaController {

    private final SnapshotService snapshotService;
    private final OperatorContext operatorContext;

    public MetaController(SnapshotService snapshotService, OperatorContext operatorContext) {
        this.snapshotService = snapshotService;
        this.operatorContext = operatorContext;
    }

    @GetMapping("/snapshot")
    public ApiResponse<SnapshotView> snapshot() {
        operatorContext.requireSuperAdmin();
        return ApiResponse.ok(snapshotService.snapshot());
    }
}
