package com.generated.platform.common;

import cn.dev33.satoken.exception.NotLoginException;
import cn.dev33.satoken.exception.NotPermissionException;
import cn.dev33.satoken.exception.NotRoleException;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.dao.DuplicateKeyException;
import org.springframework.web.servlet.resource.NoResourceFoundException;
import org.springframework.http.HttpStatus;
import org.springframework.http.ResponseEntity;
import org.springframework.validation.FieldError;
import org.springframework.web.bind.MethodArgumentNotValidException;
import org.springframework.web.bind.annotation.ExceptionHandler;
import org.springframework.web.bind.annotation.RestControllerAdvice;

/**
 * 全局异常出口。所有 Controller 只返回 {@link ApiResponse}，异常在这里收口。
 *
 * <p>HTTP 状态与业务码的关系：业务码进 body，HTTP 状态只在 4xx/5xx 时跟着走，
 * 这样 curl 能靠 -w '%{http_code}' 看出成败，前端又能拿到细分码。</p>
 */
@RestControllerAdvice
public class GlobalExceptionHandler {

    private static final Logger log = LoggerFactory.getLogger(GlobalExceptionHandler.class);

    @ExceptionHandler(BizException.class)
    public ResponseEntity<ApiResponse<Void>> onBiz(BizException e) {
        ErrorCode code = e.getCode();
        return ResponseEntity.status(httpStatusOf(code)).body(ApiResponse.fail(code, e.getMessage()));
    }

    /**
     * 未登录：HTTP 401 但不占用九种业务码——「没登录」与「令牌过期」的区别就靠这一点，
     * 前端见 401 且 code=401 是没登录，见 code=401 且 TOKEN_STALE 才是权限变了。
     */
    @ExceptionHandler(NotLoginException.class)
    public ResponseEntity<ApiResponse<Void>> onNotLogin(NotLoginException e) {
        String msg = switch (e.getType()) {
            case NotLoginException.NOT_TOKEN -> "未提供令牌";
            case NotLoginException.TOKEN_TIMEOUT -> "令牌已过期";
            case NotLoginException.INVALID_TOKEN -> "令牌无效";
            case NotLoginException.BE_REPLACED -> "账号已在别处登录";
            case NotLoginException.KICK_OUT -> "账号已被踢下线";
            default -> "未登录";
        };
        return ResponseEntity.status(HttpStatus.UNAUTHORIZED)
                .body(new ApiResponse<>(HttpStatus.UNAUTHORIZED.value(), msg, null));
    }

    /**
     * 已登录但没有该权限码。403 与上面的 401 必须分开：
     * 前端对 401 的处理是跳登录页，对 403 的处理是提示「无权操作」并留在原地。
     */
    @ExceptionHandler(NotPermissionException.class)
    public ResponseEntity<ApiResponse<Void>> onNoPermission(NotPermissionException e) {
        return ResponseEntity.status(HttpStatus.FORBIDDEN)
                .body(ApiResponse.fail(ErrorCode.FORBIDDEN, "无此操作权限：" + e.getPermission()));
    }

    @ExceptionHandler(NotRoleException.class)
    public ResponseEntity<ApiResponse<Void>> onNoRole(NotRoleException e) {
        return ResponseEntity.status(HttpStatus.FORBIDDEN)
                .body(ApiResponse.fail(ErrorCode.FORBIDDEN, "无此角色：" + e.getRole()));
    }

    /**
     * 唯一约束冲突。落到这里说明校验段没挡住（例如并发插入同一个订单号），
     * 语义是 409 而不是 500——调用方重试或换值即可。
     */
    @ExceptionHandler(DuplicateKeyException.class)
    public ResponseEntity<ApiResponse<Void>> onDuplicate(DuplicateKeyException e) {
        return ResponseEntity.status(HttpStatus.CONFLICT)
                .body(ApiResponse.fail(ErrorCode.CONFLICT, "记录已存在：唯一约束冲突"));
    }

    /** 没有这个路由：返回 404 而不是 500——路径写错是调用方的问题，不是服务端故障 */
    @ExceptionHandler(NoResourceFoundException.class)
    public ResponseEntity<ApiResponse<Void>> onNoRoute(NoResourceFoundException e) {
        return ResponseEntity.status(HttpStatus.NOT_FOUND)
                .body(ApiResponse.fail(ErrorCode.NOT_FOUND, "接口不存在：" + e.getResourcePath()));
    }

    @ExceptionHandler(MethodArgumentNotValidException.class)
    public ResponseEntity<ApiResponse<Void>> onInvalid(MethodArgumentNotValidException e) {
        FieldError first = e.getBindingResult().getFieldError();
        String msg = first == null ? "参数不合法" : first.getField() + " " + first.getDefaultMessage();
        return ResponseEntity.badRequest().body(ApiResponse.fail(ErrorCode.BAD_REQUEST, msg));
    }

    /**
     * 请求体不可读（JSON 语法错、日期格式不对等）：400 而不是 500——
     * 这是调用方的错，吞成 500 会把排障方向引到服务端（实测踩过：
     * DATETIME 传「2026-11-10 10:00:00」而 Jackson 要 ISO 的 T 分隔，报错只剩一句 500）。
     */
    @ExceptionHandler(org.springframework.http.converter.HttpMessageNotReadableException.class)
    public ResponseEntity<ApiResponse<Void>> onUnreadable(
            org.springframework.http.converter.HttpMessageNotReadableException e) {
        String detail = e.getMostSpecificCause().getMessage();
        String msg = detail == null ? "请求体不可读" : "请求体不可读：" + detail;
        return ResponseEntity.badRequest().body(ApiResponse.fail(ErrorCode.BAD_REQUEST, msg));
    }

    @ExceptionHandler(Exception.class)
    public ResponseEntity<ApiResponse<Void>> onOther(Exception e) {
        log.error("未预期异常", e);
        return ResponseEntity.status(HttpStatus.INTERNAL_SERVER_ERROR)
                .body(ApiResponse.fail(ErrorCode.SERVER_ERROR, "服务端异常"));
    }

    private HttpStatus httpStatusOf(ErrorCode code) {
        HttpStatus status = HttpStatus.resolve(code.code());
        return status == null ? HttpStatus.OK : status;
    }
}
