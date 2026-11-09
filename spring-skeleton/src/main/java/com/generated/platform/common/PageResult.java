package com.generated.platform.common;

import java.util.List;

/**
 * 分页结果，形态见《通用数据接口设计》4 节：rows / total / page / size / truncated。
 *
 * <p>{@code total} 由独立 COUNT 得出，不用已废弃的 SQL_CALC_FOUND_ROWS；
 * {@code truncated} 让 Agent 能说「共 128 条，已显示前 20 条」，
 * 而不是把整个结果集灌进上下文。</p>
 */
public record PageResult<T>(List<T> rows, long total, int page, int size, boolean truncated) {
}
