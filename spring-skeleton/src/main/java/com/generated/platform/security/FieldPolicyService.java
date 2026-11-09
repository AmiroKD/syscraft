package com.generated.platform.security;

import org.springframework.stereotype.Service;

import java.time.LocalDateTime;
import java.util.HashMap;
import java.util.List;
import java.util.Map;
import java.util.concurrent.ConcurrentHashMap;

/**
 * 字段可见策略（adm_column_policy）：PLAIN 明文 / MASK 掩码 / HIDDEN 隐藏。
 *
 * <p>三层防线各管一段（权限 4.5 / 接口清单 6）：</p>
 * <ol>
 *   <li><b>生成期</b>：标记 HIDDEN 的字段不进 VO——类型层面就不存在，不是「返回了再藏」；</li>
 *   <li><b>运行期</b>：本服务对响应做二次裁剪。覆盖「生成之后策略被改成 HIDDEN」的情况
 *       （改策略不重渲染，生成器 9 节），也执行 MASK；</li>
 *   <li><b>边界</b>：数据离开 Spring 就会落进对话记录、日志、事件流三处，所以裁剪
 *       必须在这里做完，绝不能指望 Agent 或前端自觉。</li>
 * </ol>
 *
 * <p>策略是按实体（不分角色）的全局设置：字段要么敏感要么不敏感，
 * 「谁能看明文」的粒度由表级授权负责——两层权限各管维度，不叠加语义。</p>
 */
@Service
public class FieldPolicyService {

    /** 与设计示例 138****5678 同一形态：保头尾、中间全星 */
    private static final long TTL_SECONDS = 60;

    private final org.springframework.jdbc.core.JdbcTemplate admJdbc;
    private final Map<String, Map<String, String>> cache = new ConcurrentHashMap<>();
    private volatile LocalDateTime loadedAt = LocalDateTime.MIN;

    public FieldPolicyService(@org.springframework.beans.factory.annotation.Qualifier("admJdbcTemplate")
                              org.springframework.jdbc.core.JdbcTemplate admJdbc) {
        this.admJdbc = admJdbc;
    }

    /**
     * 按策略裁剪一行数据。HIDDEN 删键；MASK 掩码字符串，非字符串值没有「部分可见」的语义，
     * 按隐藏处理（金额打星只会制造对不上账的困惑）。
     */
    public void apply(String entity, List<Map<String, Object>> rows) {
        Map<String, String> policy = policyOf(entity);
        if (policy.isEmpty()) {
            return;
        }
        for (Map<String, Object> row : rows) {
            for (String field : policy.keySet()) {
                if (!row.containsKey(field)) {
                    continue;
                }
                String visibility = policy.get(field);
                if ("HIDDEN".equals(visibility)) {
                    row.remove(field);
                } else if ("MASK".equals(visibility)) {
                    Object value = row.get(field);
                    if (value instanceof String text) {
                        row.put(field, mask(text));
                    } else {
                        row.remove(field);
                    }
                }
            }
        }
    }

    /** 138****5678 形态：保留前 3 后 4，中间全星；太短的没有可保留的头部，整串打星 */
    static String mask(String text) {
        int length = text.length();
        if (length <= 4) {
            return "*".repeat(length);
        }
        int head = Math.min(3, length - 4);
        return text.substring(0, head) + "*".repeat(length - head - 4) + text.substring(length - 4);
    }

    private Map<String, String> policyOf(String entity) {
        if (cache.containsKey(entity)
                && LocalDateTime.now().isBefore(loadedAt.plusSeconds(TTL_SECONDS))) {
            Map<String, String> cached = cache.get(entity);
            return cached == null ? Map.of() : cached;
        }
        loadAll();
        Map<String, String> cached = cache.get(entity);
        return cached == null ? Map.of() : cached;
    }

    private synchronized void loadAll() {
        if (LocalDateTime.now().isBefore(loadedAt.plusSeconds(TTL_SECONDS)) && !cache.isEmpty()) {
            return;
        }
        Map<String, Map<String, String>> loaded = new HashMap<>();
        admJdbc.query(
                "SELECT entity, field, visibility FROM adm_column_policy",
                rs -> {
                    loaded.computeIfAbsent(rs.getString(1), k -> new HashMap<>())
                          .put(rs.getString(2), rs.getString(3));
                });
        cache.clear();
        cache.putAll(loaded);
        loadedAt = LocalDateTime.now();
    }
}
