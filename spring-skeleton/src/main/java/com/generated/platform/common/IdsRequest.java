package com.generated.platform.common;

import java.util.List;

/**
 * 删除入参：按主键批量删。
 *
 * <p>不做「按条件删」——条件删的影响面无法在预告时算准，
 * 而确认令牌的指纹里必须包含影响行数（权限 4.7）。</p>
 *
 * <p>{@code confirmToken} 来自 /api/confirm/preview 的预告；
 * 删除属于高危操作，没有预告令牌的删除会被核销环节直接拒绝。</p>
 */
public record IdsRequest(List<Long> ids, String confirmToken) {

    /** 兼容不带令牌的旧调用方（预告流程会重新构造） */
    public IdsRequest(List<Long> ids) {
        this(ids, null);
    }
}
