package com.generated.platform.common;

import com.baomidou.mybatisplus.core.conditions.query.LambdaQueryWrapper;
import com.baomidou.mybatisplus.core.conditions.query.QueryWrapper;
import com.baomidou.mybatisplus.core.toolkit.support.SFunction;

import java.util.ArrayList;
import java.util.Collection;
import java.util.List;

/**
 * 把一条 {@link Cond} 落到条件构造器上。<b>与实体无关</b>，所以放在 platform：
 * 生成的 Service 只负责「字段名 → 方法引用」的 switch，操作符语义统一在这里。
 *
 * <p>为什么字段用方法引用：字段改名时编译期就能发现，而不是运行时报 Unknown column
 * （《代码生成器设计》3.2）。</p>
 *
 * <p>为什么操作符要白名单：任何自由字符串拼进 SQL 都是注入面，
 * 即使是生成期决定的字段名也不例外。</p>
 */
public final class QueryOps {

    private QueryOps() {
    }

    public static <T> void apply(LambdaQueryWrapper<T> wrapper, SFunction<T, ?> column, Cond cond) {
        String operator = cond.operator() == null ? "EQ" : cond.operator().toUpperCase();
        switch (operator) {
            case "EQ" -> wrapper.eq(column, cond.value());
            case "NE" -> wrapper.ne(column, cond.value());
            case "GT" -> wrapper.gt(column, cond.value());
            case "GE" -> wrapper.ge(column, cond.value());
            case "LT" -> wrapper.lt(column, cond.value());
            case "LE" -> wrapper.le(column, cond.value());
            case "LIKE" -> wrapper.like(column, escapeLike(cond.value()));
            case "IN" -> wrapper.in(column, requireList(cond, "IN"));
            case "BETWEEN" -> {
                List<?> range = requireList(cond, "BETWEEN");
                if (range.size() != 2) {
                    throw new BizException(ErrorCode.BAD_REQUEST, "BETWEEN 的值必须是二元数组");
                }
                wrapper.between(column, range.get(0), range.get(1));
            }
            case "IS_NULL" -> wrapper.isNull(column);
            default -> throw new BizException(ErrorCode.BAD_REQUEST, "不支持的操作符：" + cond.operator());
        }
    }

    /**
     * 列名字符串版本，只给聚合用——聚合要写 {@code GROUP BY} 与 {@code SUM(col)}，
     * 方法引用在那里没有落点。列名不是外部输入，来自生成期的字段白名单。
     */
    public static <T> void apply(QueryWrapper<T> wrapper, String column, Cond cond) {
        String operator = cond.operator() == null ? "EQ" : cond.operator().toUpperCase();
        switch (operator) {
            case "EQ" -> wrapper.eq(column, cond.value());
            case "NE" -> wrapper.ne(column, cond.value());
            case "GT" -> wrapper.gt(column, cond.value());
            case "GE" -> wrapper.ge(column, cond.value());
            case "LT" -> wrapper.lt(column, cond.value());
            case "LE" -> wrapper.le(column, cond.value());
            case "LIKE" -> wrapper.like(column, escapeLike(cond.value()));
            case "IN" -> wrapper.in(column, requireList(cond, "IN"));
            case "BETWEEN" -> {
                List<?> range = requireList(cond, "BETWEEN");
                if (range.size() != 2) {
                    throw new BizException(ErrorCode.BAD_REQUEST, "BETWEEN 的值必须是二元数组");
                }
                wrapper.between(column, range.get(0), range.get(1));
            }
            case "IS_NULL" -> wrapper.isNull(column);
            default -> throw new BizException(ErrorCode.BAD_REQUEST, "不支持的操作符：" + cond.operator());
        }
    }

    /**
     * LIKE 的值必须转义 % 与 _：不转义的话，用户输入一个 % 就是全表扫描。
     * 只做长度与数量校验，不做类型校验——类型错配交给 MySQL 报错，比在 Java 里猜更准。
     */
    private static String escapeLike(Object value) {
        if (value == null) {
            return "";
        }
        return value.toString()
                .replace("\\", "\\\\")
                .replace("%", "\\%")
                .replace("_", "\\_");
    }

    private static List<?> requireList(Cond cond, String operator) {
        Object value = cond.value();
        if (value instanceof Collection<?> collection) {
            List<Object> list = new ArrayList<>(collection);
            if (list.isEmpty()) {
                throw new BizException(ErrorCode.BAD_REQUEST, operator + " 的值不能是空数组");
            }
            if (list.size() > 100) {
                throw new BizException(ErrorCode.BAD_REQUEST, operator + " 的元素上限为 100");
            }
            return list;
        }
        if (value instanceof Object[] array) {
            return List.of(array);
        }
        throw new BizException(ErrorCode.BAD_REQUEST, operator + " 的值必须是数组");
    }
}
