package com.generated.platform.common;

import java.util.List;

/**
 * 条件组：顶层 AND / OR + 一组条件，<b>不支持任意嵌套</b>（《通用数据接口设计》3.2）。
 *
 * <p>嵌套越深，模型生成的正确率越低，校验复杂度却呈指数上升；
 * 更复杂的查询由 Agent 拆成多次调用，在对话层合并。</p>
 */
public record FilterGroup(String op, List<Cond> conditions) {

    public boolean isOr() {
        return "OR".equalsIgnoreCase(op);
    }
}
