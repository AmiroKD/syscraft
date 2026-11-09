package com.generated.platform.probe;

import cn.dev33.satoken.annotation.SaCheckPermission;
import com.generated.platform.common.ApiResponse;
import org.springframework.context.annotation.Profile;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RestController;

import java.util.LinkedHashMap;
import java.util.Map;

/**
 * 权限码探针，<b>只用于 §5 第 3 步验收</b>，默认 profile 不加载。
 *
 * <p>为什么需要它：真正的业务 Controller 要等模板生成（第 4 步）才出现，
 * 而「同一接口两种角色一个 200 一个 403」需要现在就有一个带
 * {@code @SaCheckPermission} 的端点可打。这两个方法模仿生成器的产物形状，
 * 业务代码一行没有，第 4 步真实 Controller 落地后直接删掉本类。</p>
 *
 * <p>启用：{@code --spring.profiles.active=probe}</p>
 */
@Profile("probe")
@RestController
@RequestMapping("/api/probe")
public class PermissionProbeController {

    @SaCheckPermission("biz:order:read")
    @GetMapping("/order/read")
    public ApiResponse<Map<String, Object>> read() {
        return ApiResponse.ok(payload("biz:order:read"));
    }

    @SaCheckPermission("biz:order:delete")
    @GetMapping("/order/delete")
    public ApiResponse<Map<String, Object>> delete() {
        return ApiResponse.ok(payload("biz:order:delete"));
    }

    private Map<String, Object> payload(String code) {
        Map<String, Object> body = new LinkedHashMap<>();
        body.put("checked", code);
        body.put("note", "探针端点，仅第 3 步验收使用");
        return body;
    }
}
