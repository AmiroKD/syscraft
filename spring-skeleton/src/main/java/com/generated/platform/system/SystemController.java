package com.generated.platform.system;

import com.generated.platform.admin.OperatorContext;
import com.generated.platform.common.ApiResponse;
import com.generated.platform.common.ErrorCode;
import jakarta.validation.Valid;
import jakarta.validation.constraints.NotBlank;
import jakarta.validation.constraints.NotNull;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.PutMapping;
import org.springframework.web.bind.annotation.RequestBody;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RestController;

/**
 * 系统状态与模型配置（契约 2.2 的 5 个端点）。
 *
 * <p>GET state 所有登录用户可见（首次引导要靠它判断）；其余仅超管。
 * 配置的读写与连通性测试都在 Service——将来「批量导入」等第二条路也要用。</p>
 */
@RestController
@RequestMapping("/api/system")
public class SystemController {

    public record ModelRequest(@NotBlank String provider, @NotBlank String baseUrl,
                               @NotBlank String model, String apiKey) {
    }

    private final SysStateService sysStateService;
    private final ModelConfigService modelConfigService;
    private final OperatorContext operatorContext;

    public SystemController(SysStateService sysStateService, ModelConfigService modelConfigService,
                            OperatorContext operatorContext) {
        this.sysStateService = sysStateService;
        this.modelConfigService = modelConfigService;
        this.operatorContext = operatorContext;
    }

    @GetMapping("/state")
    public ApiResponse<SysStateService.SysState> state() {
        return ApiResponse.ok(sysStateService.state());
    }

    /** initialized 与 currentVersion 至少给一个；都为空是调用方的 bug */
    public record StateRequest(Boolean initialized, String currentVersion) {
    }

    @PutMapping("/state")
    public ApiResponse<Void> updateState(@RequestBody StateRequest request) {
        operatorContext.requireSuperAdmin();
        if (request.currentVersion() != null && !request.currentVersion().isBlank()) {
            sysStateService.setCurrentVersion(request.currentVersion());
        }
        if (request.initialized() != null) {
            sysStateService.setInitialized(request.initialized());
        }
        if (request.initialized() == null && request.currentVersion() == null) {
            return ApiResponse.fail(ErrorCode.BAD_REQUEST, "initialized 与 currentVersion 至少给一个");
        }
        return ApiResponse.ok();
    }

    @GetMapping("/readiness")
    public ApiResponse<SysStateService.Readiness> readiness() {
        return ApiResponse.ok(sysStateService.readiness());
    }

    /** 界面回显：Key 打码为「已配置 + 尾 4 位」，明文永远不回传 */
    @GetMapping("/model")
    public ApiResponse<ModelConfigService.ModelConfig> model() {
        operatorContext.requireSuperAdmin();
        return ApiResponse.ok(modelConfigService.get());
    }

    @PutMapping("/model")
    public ApiResponse<Void> saveModel(@Valid @RequestBody ModelRequest request) {
        operatorContext.requireSuperAdmin();
        modelConfigService.save(new ModelConfigService.ModelSaveRequest(
                request.provider(), request.baseUrl(), request.model(), request.apiKey()));
        return ApiResponse.ok();
    }

    /** 用已保存的配置做连通性测试：把「填错 Key」拦在保存的那一刻，而不是下一次对话 */
    @PostMapping("/model/test")
    public ApiResponse<ModelConfigService.TestResult> testModel() {
        operatorContext.requireSuperAdmin();
        return ApiResponse.ok(modelConfigService.test());
    }
}
