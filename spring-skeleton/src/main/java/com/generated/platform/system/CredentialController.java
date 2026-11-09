package com.generated.platform.system;

import com.generated.platform.admin.OperatorContext;
import com.generated.platform.common.ApiResponse;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.RestController;

/**
 * 模型凭据的读取端点——对契约的**明示扩展**。
 *
 * <p>设计里模型配置存产物侧（AES-GCM 加密入库），而对话解析在平台侧：
 * 平台服务需要一个受控的明文出口（仅超管令牌可读），否则设置面板录入的
 * 配置永远到不了解析器。明文只走这条受控通道，绝不进日志。</p>
 */
@RestController
public class CredentialController {

    private final ModelConfigService modelConfigService;
    private final OperatorContext operatorContext;

    public CredentialController(ModelConfigService modelConfigService, OperatorContext operatorContext) {
        this.modelConfigService = modelConfigService;
        this.operatorContext = operatorContext;
    }

    @GetMapping("/api/system/model/credential")
    public ApiResponse<ModelConfigService.Credential> credential() {
        operatorContext.requireSuperAdmin();
        return ApiResponse.ok(modelConfigService.credential());
    }
}
