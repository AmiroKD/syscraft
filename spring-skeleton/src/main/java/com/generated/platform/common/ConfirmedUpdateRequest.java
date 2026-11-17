package com.generated.platform.common;

import java.util.List;
import java.util.Map;

/**
 * 确认后的批量更新请求（协议 4.7：修改与删除同为高危，走预告—确认—执行）。
 *
 * <p>{@code values} 是「将要应用的字段值」（只放要改的字段，键为列名）；
 * {@code valuesHash} 是调用方对 values 规范 JSON 的 sha256，预告与执行各带一份
 * 进指纹——把「用户确认的改成什么」与「真正写库的内容」绑定。服务端按
 * 「现有行 + values 覆盖」合并后走与单条更新完全相同的校验、规则与状态机。</p>
 */
public record ConfirmedUpdateRequest(List<Long> ids, Map<String, Object> values,
                                     String valuesHash, String confirmToken) {
}
