package com.generated.platform.admin;

import com.generated.platform.admin.dto.AdminDtos.GrantRequest;
import com.generated.platform.admin.dto.AdminDtos.GrantView;
import com.generated.platform.common.ApiResponse;
import jakarta.validation.Valid;
import org.springframework.web.bind.annotation.DeleteMapping;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.PathVariable;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.RequestBody;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RequestParam;
import org.springframework.web.bind.annotation.RestController;

import java.util.List;
import java.util.Map;

/**
 * 表级授权接口。授权是整个体系里最容易被一句话打穿的面
 * （「给所有人都开所有表」），所以这里刻意没有批量端点。
 */
@RestController
@RequestMapping("/api/admin/grants")
public class GrantController {

    private final GrantService grantService;

    public GrantController(GrantService grantService) {
        this.grantService = grantService;
    }

    @GetMapping
    public ApiResponse<List<GrantView>> list(
            @RequestParam(required = false) String subjectType,
            @RequestParam(required = false) Long subjectId) {
        return ApiResponse.ok(grantService.list(subjectType, subjectId));
    }

    @PostMapping
    public ApiResponse<GrantView> grant(@Valid @RequestBody GrantRequest request) {
        return ApiResponse.ok(grantService.grant(request));
    }

    /**
     * 生成流水线的默认授权引导（明示扩展，仅超管）：新表落地后批量应用
     * 模型 grants 段，取代 Python 的 mysql 桥接。逐条幂等。
     */
    @PostMapping("/bootstrap")
    public ApiResponse<Map<String, Integer>> bootstrap(
            @RequestBody List<GrantService.BootstrapGrant> grants) {
        return ApiResponse.ok(grantService.bootstrap(grants));
    }

    @DeleteMapping("/{id}")
    public ApiResponse<Void> revoke(@PathVariable long id) {
        grantService.revoke(id);
        return ApiResponse.ok();
    }
}
