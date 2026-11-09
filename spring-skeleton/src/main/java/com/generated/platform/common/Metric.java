package com.generated.platform.common;

/**
 * 聚合指标。func 封闭为 COUNT / SUM / AVG / MAX / MIN；
 * SUM 与 AVG 只能作用于数值字段，由生成期已知的类型决定，运行期拒绝其它组合。
 */
public record Metric(String func, String field, String alias) {
}
