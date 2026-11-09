package com.generated.platform.common;

/**
 * 单个查询条件。operator 是枚举而非自由字符串：{@code EQ NE GT GE LT LE LIKE IN BETWEEN IS_NULL}。
 *
 * <p>value 用 Object：JSON 里可能是数字、字符串、布尔或二元数组，
 * 具体类型由 QueryOps 结合字段类型决定。</p>
 */
public record Cond(String field, String operator, Object value) {
}
