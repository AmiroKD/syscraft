package com.generated.platform.common;

/**
 * 排序项。direction 只接受 ASC / DESC，其它值一律拒绝——
 * 自由字符串拼进 SQL 就是注入面，即使是生成期决定的列名。
 */
public record OrderBy(String field, String direction) {

    public boolean isDesc() {
        return "DESC".equalsIgnoreCase(direction);
    }
}
