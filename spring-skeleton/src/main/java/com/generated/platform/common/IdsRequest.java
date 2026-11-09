package com.generated.platform.common;

import java.util.List;

/**
 * 删除入参：按主键批量删。
 *
 * <p>不做「按条件删」——条件删的影响面无法在预告时算准，
 * 而确认令牌的指纹里必须包含影响行数（权限 4.7）。</p>
 */
public record IdsRequest(List<Long> ids) {
}
