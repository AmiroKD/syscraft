package com.generated.platform.common;

/**
 * 业务异常。抛出后由 {@link GlobalExceptionHandler} 转成 {@link ApiResponse}。
 *
 * <p>约定：只有「预期内的失败」才抛这个类（口令错、无权限、状态冲突）；
 * 预期外的异常一律落到 500，不包装成业务码——包装会把 bug 伪装成正常业务分支。</p>
 */
public class BizException extends RuntimeException {

    private final ErrorCode code;

    public BizException(ErrorCode code, String msg) {
        super(msg);
        this.code = code;
    }

    public ErrorCode getCode() {
        return code;
    }
}
