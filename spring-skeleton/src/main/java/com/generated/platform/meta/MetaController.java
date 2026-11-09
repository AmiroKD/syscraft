package com.generated.platform.meta;

import cn.dev33.satoken.stp.StpUtil;
import com.generated.platform.admin.OperatorContext;
import com.generated.platform.admin.entity.ModelSnapshotEntity;
import com.generated.platform.admin.entity.StateEntity;
import com.generated.platform.admin.mapper.ModelSnapshotMapper;
import com.generated.platform.admin.mapper.StateMapper;
import com.generated.platform.common.ApiResponse;
import com.generated.platform.common.BizException;
import com.generated.platform.common.ErrorCode;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.RequestBody;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RequestParam;
import org.springframework.web.bind.annotation.RestController;

import java.util.Map;

/**
 * 元数据查询（契约 2.4 的三个端点 + 一个写扩展）。
 *
 * <p>capabilities 给所有登录用户（体验层副本，安全仍靠逐次重判）；
 * snapshot 与 model 只给超管（全量结构与需求原文都是生成模式的输入）。
 * POST /api/meta/model 是对契约的**明示扩展**：快照表要有写入者，
 * 流水线在生成完成后上传模型原件，否则 GET 无从谈起。</p>
 */
@RestController
@RequestMapping("/api/meta")
public class MetaController {

    private final SnapshotService snapshotService;
    private final CapabilityService capabilityService;
    private final ModelSnapshotMapper modelSnapshotMapper;
    private final StateMapper stateMapper;
    private final OperatorContext operatorContext;

    public MetaController(SnapshotService snapshotService, CapabilityService capabilityService,
                          ModelSnapshotMapper modelSnapshotMapper, StateMapper stateMapper,
                          OperatorContext operatorContext) {
        this.snapshotService = snapshotService;
        this.capabilityService = capabilityService;
        this.modelSnapshotMapper = modelSnapshotMapper;
        this.stateMapper = stateMapper;
        this.operatorContext = operatorContext;
    }

    @GetMapping("/snapshot")
    public ApiResponse<SnapshotView> snapshot() {
        operatorContext.requireSuperAdmin();
        return ApiResponse.ok(snapshotService.snapshot());
    }

    /** 能力视图：可访问实体 × 可用操作 × 可读字段，带快照版本号 */
    @GetMapping("/capabilities")
    public ApiResponse<Map<String, Object>> capabilities() {
        var operator = operatorContext.current();
        boolean superAdmin = "SUPER_ADMIN".equals(operator.roleCode());
        return ApiResponse.ok(capabilityService.view(operator.userId(), operator.roleId(), superAdmin));
    }

    /**
     * 业务模型快照。version 入参形如 "v5"：表主键是 BIGINT，取尾数存储。
     * 缺省取 adm_state.model_version 指的当前版本。
     */
    @GetMapping("/model")
    public ApiResponse<Object> model(@RequestParam(required = false) String version) {
        operatorContext.requireSuperAdmin();
        Long numeric = version != null ? parseVersion(version) : currentModelVersion();
        if (numeric == null) {
            throw new BizException(ErrorCode.NOT_FOUND, "没有可用的模型快照");
        }
        ModelSnapshotEntity row = modelSnapshotMapper.selectById(numeric);
        if (row == null) {
            throw new BizException(ErrorCode.NOT_FOUND, "模型快照 v" + numeric + " 不存在");
        }
        return ApiResponse.ok(Map.of("version", row.getVersion(), "model", row.getModelJson(),
                "createdAt", row.getCreatedAt().toString()));
    }

    /** 明示扩展：流水线上传模型原件（仅超管）。幂等：同版本覆盖；同时把「当前模型版本」指过去 */
    @PostMapping("/model")
    public ApiResponse<Void> saveModel(@RequestBody ModelUpload upload) {
        operatorContext.requireSuperAdmin();
        Long numeric = parseVersion(upload.version());
        if (numeric == null) {
            throw new BizException(ErrorCode.BAD_REQUEST, "version 必须形如 v5");
        }
        ModelSnapshotEntity entity = new ModelSnapshotEntity();
        entity.setVersion(numeric);
        entity.setModelJson(upload.modelJson());
        entity.setCreatedBy(StpUtil.getLoginIdAsLong());
        modelSnapshotMapper.insertOrUpdate(entity);
        // 当前模型版本同步推进：GET 不带 version 时才找得到最新快照
        StateEntity state = stateMapper.selectById(1);
        if (state != null) {
            state.setModelVersion(numeric);
            stateMapper.updateById(state);
        }
        return ApiResponse.ok();
    }

    public record ModelUpload(String version, String modelJson) {
    }

    private Long currentModelVersion() {
        StateEntity state = stateMapper.selectById(1);
        return state == null ? null : state.getModelVersion();
    }

    private Long parseVersion(String version) {
        var matcher = java.util.regex.Pattern.compile("(\\d+)").matcher(version == null ? "" : version);
        return matcher.find() ? Long.parseLong(matcher.group(1)) : null;
    }
}
