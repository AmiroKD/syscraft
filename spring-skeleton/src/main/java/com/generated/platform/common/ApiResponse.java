package com.generated.platform.common;

/**
 * 统一响应体。code 取 {@link ErrorCode}，msg 只给人看，data 为载荷。
 *
 * <p>不用 HTTP 状态码区分业务结果：生成流程有 PENDING(202)、编译失败有 BUILD_FAILED(422)，
 * 这些都不是标准 HTTP 语义能表达的。HTTP 状态码只用于「请求本身是否被正确处理」。</p>
 */
public record ApiResponse<T>(int code, String msg, T data) {

    public static <T> ApiResponse<T> ok(T data) {
        return new ApiResponse<>(ErrorCode.OK.code(), "ok", data);
    }

    public static ApiResponse<Void> ok() {
        return ok(null);
    }

    public static <T> ApiResponse<T> fail(ErrorCode code, String msg) {
        return new ApiResponse<>(code.code(), msg, null);
    }
}
