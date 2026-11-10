package com.generated.platform.admin;

import cn.dev33.satoken.stp.StpUtil;
import cn.dev33.satoken.stp.StpUtil;
import com.generated.platform.admin.dto.AdminDtos.FeatureRequestHandleRequest;
import com.generated.platform.admin.dto.AdminDtos.FeatureRequestReport;
import com.generated.platform.admin.dto.AdminDtos.FeatureRequestView;
import com.generated.platform.admin.entity.FeatureRequestEntity;
import com.generated.platform.admin.entity.UserEntity;
import com.generated.platform.admin.mapper.UserMapper;
import com.generated.platform.auth.TokenService;
import com.generated.platform.common.ApiResponse;
import jakarta.validation.Valid;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.PathVariable;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.PutMapping;
import org.springframework.web.bind.annotation.RequestBody;
import org.springframework.web.bind.annotation.RequestParam;
import org.springframework.web.bind.annotation.RestController;

import java.util.List;
import java.util.Map;

/**
 * 需求池接口（底座扩展 v0.1）。
 *
 * <p>上报是所有登录用户的权利（登记不是提权，认证由拦截器统一把守）；
 * 查看需要 MANAGE_USER（管理员及以上）；处理仅超管——采纳等于决定
 * 「下一个生成什么」，与生成触发同权（缺陷审查 P0-2）。</p>
 */
@RestController
public class FeatureRequestController {

    private final FeatureRequestService requestService;
    private final TokenService tokenService;
    private final UserMapper userMapper;

    public FeatureRequestController(FeatureRequestService requestService, TokenService tokenService,
                                    UserMapper userMapper) {
        this.requestService = requestService;
        this.tokenService = tokenService;
        this.userMapper = userMapper;
    }

    /** 对话兜底 / 用户主动上报。source 固定 CHAT_FALLBACK，管理端录入走同一路径换 source */
    @PostMapping("/api/feature-requests")
    public ApiResponse<Map<String, Long>> report(@Valid @RequestBody FeatureRequestReport body) {
        long userId = tokenService.currentUserId();
        long id = requestService.report(userId, body.rawText(), body.wantedEntity(), "CHAT_FALLBACK");
        return ApiResponse.ok(Map.of("id", id));
    }

    @GetMapping("/api/admin/feature-requests")
    public ApiResponse<List<FeatureRequestView>> list(
            @RequestParam(required = false) String status) {
        return ApiResponse.ok(requestService.list(status).stream().map(this::toView).toList());
    }

    @PutMapping("/api/admin/feature-requests/{id}")
    public ApiResponse<Void> handle(@PathVariable long id,
                                    @Valid @RequestBody FeatureRequestHandleRequest body) {
        requestService.handle(id, body.status(), body.note());
        return ApiResponse.ok();
    }

    private FeatureRequestView toView(FeatureRequestEntity row) {
        String username = usernameOf(row.getUserId());
        String handledByName = usernameOf(row.getHandledBy());
        return new FeatureRequestView(row.getId(), row.getUserId(), username, row.getRawText(),
                row.getWantedEntity(), row.getSource(), row.getStatus(),
                row.getHandledBy(), handledByName, row.getHandledAt(),
                row.getNote(), row.getCreatedAt());
    }

    private String usernameOf(Long userId) {
        if (userId == null) {
            return null;
        }
        UserEntity user = userMapper.selectById(userId);
        return user == null ? null : user.getUsername();
    }
}
