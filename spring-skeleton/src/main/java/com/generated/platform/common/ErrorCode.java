package com.generated.platform.common;

/**
 * 业务错误码，与《平台与产物接口契约 v1.1》3 节、接口清单 7 节对齐。
 *
 * <p>只用这九个。{@link #TOKEN_STALE} 是 401 的细分：前端据此区分
 * 「没登录」（HTTP 401、无业务码）与「权限被改了、需重登」。</p>
 */
public enum ErrorCode {

    /** 成功 */
    OK(0),
    /** 生成中，稍后取结果 */
    PENDING(202),
    /** 参数不合法 */
    BAD_REQUEST(400),
    /** 令牌所载权限版本已过期，需重登 */
    TOKEN_STALE(401),
    /** 已登录但无此操作权限 */
    FORBIDDEN(403),
    /** 目标不存在 */
    NOT_FOUND(404),
    /** 状态冲突（重名、令牌已被核销、角色下仍有用户） */
    CONFLICT(409),
    /** 生成物编译失败 */
    BUILD_FAILED(422),
    /** 服务端异常 */
    SERVER_ERROR(500);

    private final int code;

    ErrorCode(int code) {
        this.code = code;
    }

    public int code() {
        return code;
    }
}
