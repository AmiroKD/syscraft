package com.generated.platform.meta;

import com.baomidou.mybatisplus.core.toolkit.Wrappers;
import com.generated.platform.admin.entity.EntitySnapshotEntity;
import com.generated.platform.admin.entity.FieldSnapshotEntity;
import com.generated.platform.admin.entity.StateEntity;
import com.generated.platform.admin.mapper.EntitySnapshotMapper;
import com.generated.platform.admin.mapper.FieldSnapshotMapper;
import com.generated.platform.admin.mapper.StateMapper;
import org.springframework.stereotype.Service;

import java.util.ArrayList;
import java.util.Comparator;
import java.util.HashSet;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.Set;

/**
 * 能力视图（契约 2.4）：当前用户「可访问实体 × 可用操作 × 可读字段」。
 *
 * <p>这是「给对话用」的副本——提前告知用户做不到什么，体验层；执行时 Spring
 * 仍逐次重判（权限码），两层缺一不可。数据全部来自快照与授权表，不查业务库。</p>
 */
@Service
public class CapabilityService {

    /** 授权档位 → 可执行操作。档位与 GrantService 的 ACCESS_LEVELS 对齐 */
    private static final Map<String, List<String>> ACCESS_OPS = Map.of(
            "READ", List.of("query", "aggregate"),
            "READ_WRITE", List.of("query", "aggregate", "insert", "update"),
            "FULL", List.of("query", "aggregate", "insert", "update", "delete"));

    private final EntitySnapshotMapper entitySnapshotMapper;
    private final FieldSnapshotMapper fieldSnapshotMapper;
    private final GrantViewMapper grantViewMapper;
    private final StateMapper stateMapper;

    public CapabilityService(EntitySnapshotMapper entitySnapshotMapper,
                             FieldSnapshotMapper fieldSnapshotMapper,
                             GrantViewMapper grantViewMapper,
                             StateMapper stateMapper) {
        this.entitySnapshotMapper = entitySnapshotMapper;
        this.fieldSnapshotMapper = fieldSnapshotMapper;
        this.grantViewMapper = grantViewMapper;
        this.stateMapper = stateMapper;
    }

    /**
     * @param userId     当前用户
     * @param roleId     当前用户角色
     * @param superAdmin 超管不受授权行约束：能力视图对超管展示全量实体 + FULL
     *                   （执行时的权限码判定对超管本来就是全放行，视图口径必须一致）
     * @return capabilities 视图；没有任何授权时 entities 为空数组
     */
    public Map<String, Object> view(long userId, long roleId, boolean superAdmin) {
        // 1) 该用户的全部授权行（用户级 + 角色级），同实体取最高档位
        Map<String, String> accessByEntity = new LinkedHashMap<>();
        if (superAdmin) {
            for (EntitySnapshotEntity snapshot : entitySnapshotMapper.selectList(null)) {
                accessByEntity.put(snapshot.getEntity(), "FULL");
            }
        } else {
            for (GrantViewMapper.GrantRow row : grantViewMapper.rowsOf(userId, roleId)) {
                String existing = accessByEntity.get(row.entity());
                accessByEntity.put(row.entity(), maxAccess(existing, row.access()));
            }
        }

        // 2) 全局隐藏字段（列策略是实体级全局策略，不分角色）
        Set<String> hidden = new HashSet<>();
        for (var policy : grantViewMapper.hiddenFields()) {
            hidden.add(policy.entity() + "." + policy.field());
        }

        // 3) 逐实体拼装；快照里没有授权记录的实体不出现
        long version = 0;
        StateEntity state = stateMapper.selectById(1);
        if (state != null && state.getSnapshotVersion() != null) {
            version = state.getSnapshotVersion();
        }
        List<Map<String, Object>> entities = new ArrayList<>();
        for (EntitySnapshotEntity snapshot : entitySnapshotMapper.selectList(null)) {
            String access = accessByEntity.get(snapshot.getEntity());
            if (access == null) {
                continue;
            }
            Map<String, Object> item = new LinkedHashMap<>();
            item.put("entity", snapshot.getEntity());
            item.put("displayName", snapshot.getDisplayName());
            item.put("access", access);
            item.put("ops", ACCESS_OPS.getOrDefault(access, List.of()));
            List<Map<String, Object>> fields = new ArrayList<>();
            List<FieldSnapshotEntity> all = fieldSnapshotMapper.selectList(Wrappers
                    .<FieldSnapshotEntity>lambdaQuery()
                    .eq(FieldSnapshotEntity::getEntity, snapshot.getEntity())
                    .orderByAsc(FieldSnapshotEntity::getOrdinal));
            for (FieldSnapshotEntity field : all) {
                if (hidden.contains(snapshot.getEntity() + "." + field.getField())) {
                    continue; // HIDDEN 字段对视图也不可见——用户根本不该知道有这列
                }
                fields.add(Map.of("field", field.getField(), "semantic", field.getSemantic(),
                        "type", field.getDataType(),
                        "masked", "MASK".equalsIgnoreCase(field.getVisibility())));
            }
            item.put("fields", fields);
            entities.add(item);
        }
        entities.sort(Comparator.comparing(e -> String.valueOf(e.get("entity"))));
        return Map.of("version", version, "entities", entities);
    }

    private String maxAccess(String a, String b) {
        if (a == null) {
            return b;
        }
        return rank(a) >= rank(b) ? a : b;
    }

    private int rank(String access) {
        return switch (access) {
            case "FULL" -> 3;
            case "READ_WRITE" -> 2;
            default -> 1;
        };
    }
}
