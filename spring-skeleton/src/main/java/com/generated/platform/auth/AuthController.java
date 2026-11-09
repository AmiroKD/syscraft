package com.generated.platform.auth;

import com.generated.platform.auth.dto.LoginRequest;
import com.generated.platform.auth.dto.LoginResult;
import com.generated.platform.auth.dto.MeResult;
import com.generated.platform.auth.dto.PasswordRequest;
import com.generated.platform.common.ApiResponse;
import jakarta.validation.Valid;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.RequestBody;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RestController;

/**
 * 认证入口。Controller 只做参数绑定与转发，规则都在 {@link LoginService}——
 * 管理接口将来可能被「对话工具」和「批量导入」两条路调用，校验放在 Controller 会漏（接口清单 9.4）。
 */
@RestController
@RequestMapping("/api/auth")
public class AuthController {

    private final LoginService loginService;
    private final TokenService tokenService;

    public AuthController(LoginService loginService, TokenService tokenService) {
        this.loginService = loginService;
        this.tokenService = tokenService;
    }

    @PostMapping("/login")
    public ApiResponse<LoginResult> login(@Valid @RequestBody LoginRequest req) {
        return ApiResponse.ok(loginService.login(req));
    }

    /**
     * 注销。JWT Stateless 下服务端没有会话可销毁，令牌作废只能靠前端丢弃 +
     * 版本号变更（改密、停用、调角色）。这是不引入 Redis 的已知代价，写进展望。
     */
    @PostMapping("/logout")
    public ApiResponse<Void> logout() {
        return ApiResponse.ok();
    }

    @GetMapping("/me")
    public ApiResponse<MeResult> me() {
        return ApiResponse.ok(loginService.me(tokenService.currentUserId()));
    }

    @PostMapping("/password")
    public ApiResponse<Void> changePassword(@Valid @RequestBody PasswordRequest req) {
        loginService.changePassword(tokenService.currentUserId(), req);
        return ApiResponse.ok();
    }
}
