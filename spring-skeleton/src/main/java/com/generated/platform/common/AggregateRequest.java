package com.generated.platform.common;

import java.util.List;

/**
 * 聚合请求：分组列 + 指标 + 过滤条件。不支持 HAVING（列入局限），
 * 更复杂的统计由 Agent 拆成多次查询后在对话层合并。
 */
public record AggregateRequest(List<String> groupBy, List<Metric> metrics, FilterGroup filter) {
}
