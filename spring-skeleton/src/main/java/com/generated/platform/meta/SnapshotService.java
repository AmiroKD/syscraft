package com.generated.platform.meta;

import com.baomidou.mybatisplus.core.toolkit.Wrappers;
import com.generated.platform.admin.entity.EntitySnapshotEntity;
import com.generated.platform.admin.entity.FieldSnapshotEntity;
import com.generated.platform.admin.mapper.EntitySnapshotMapper;
import com.generated.platform.admin.mapper.FieldSnapshotMapper;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.beans.factory.annotation.Qualifier;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;

import java.time.LocalDateTime;
import java.util.ArrayList;
import java.util.HashMap;
import java.util.HashSet;
import java.util.List;
import java.util.Map;
import java.util.Objects;
import java.util.Set;

/**
 * 结构快照：从 {@code biz} 的 information_schema 反推，写回 {@code adm}。
 *
 * <p><b>为什么由产物自己导出，而不是让 Python 写。</b>权限库对平台侧物理不可达（权限 6 节），
 * 而契约里没有写快照的端点；让产物在启动时自查一次，既不需要新开端点，也不会出现
 * 「代码改了、快照没跟上」的第二套真相——快照永远等于当前真实结构。</p>
 *
 * <p>中文名取表注释、字段语义取列注释：生成器已经把这些写进 DDL，反推时直接读得到。</p>
 */
@Service
public class SnapshotService {

    private static final Logger log = LoggerFactory.getLogger(SnapshotService.class);

    private static final String SQL_TABLES = """
            SELECT TABLE_NAME, TABLE_COMMENT
              FROM information_schema.TABLES
             WHERE TABLE_SCHEMA = DATABASE() AND TABLE_TYPE = 'BASE TABLE'
             ORDER BY TABLE_NAME
            """;

    private static final String SQL_COLUMNS = """
            SELECT COLUMN_NAME, DATA_TYPE, IS_NULLABLE, COLUMN_KEY, COLUMN_COMMENT, ORDINAL_POSITION
              FROM information_schema.COLUMNS
             WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = ?
             ORDER BY ORDINAL_POSITION
            """;

    private final JdbcTemplate bizJdbc;
    private final JdbcTemplate admJdbc;
    private final EntitySnapshotMapper entityMapper;
    private final FieldSnapshotMapper fieldMapper;

    public SnapshotService(@Qualifier("bizJdbcTemplate") JdbcTemplate bizJdbc,
                           @Qualifier("admJdbcTemplate") JdbcTemplate admJdbc,
                           EntitySnapshotMapper entityMapper,
                           FieldSnapshotMapper fieldMapper) {
        this.bizJdbc = bizJdbc;
        this.admJdbc = admJdbc;
        this.entityMapper = entityMapper;
        this.fieldMapper = fieldMapper;
    }

    /** 供启动钩子调用。产物侧表结构没变时，这里是一串只读查询，不写库。 */
    @Transactional
    public SnapshotView refresh() {
        List<TableMeta> tables = readTables();
        Set<String> present = new HashSet<>();
        long maxVersion = 0;

        for (TableMeta table : tables) {
            present.add(table.name());
            List<ColumnMeta> columns = readColumns(table.name());

            EntitySnapshotEntity existing = entityMapper.selectById(table.name());
            List<FieldSnapshotEntity> existingFields = fieldsOf(table.name());
            if (!needsRewrite(existing, existingFields, columns)) {
                maxVersion = Math.max(maxVersion, existing == null ? 0 : existing.getVersion());
                continue;
            }

            long nextVersion = (existing == null || existing.getVersion() == null) ? 1 : existing.getVersion() + 1;
            EntitySnapshotEntity row = existing == null ? new EntitySnapshotEntity() : existing;
            row.setEntity(table.name());
            row.setDisplayName(blankTo(table.comment(), table.name()));
            row.setOwnerField(existing == null ? null : existing.getOwnerField());
            row.setFieldCount(columns.size());
            row.setVersion(nextVersion);
            row.setGeneratedAt(LocalDateTime.now());
            if (existing == null) {
                entityMapper.insert(row);
            } else {
                entityMapper.updateById(row);
            }

            rewriteFields(table.name(), columns, existingFields);
            maxVersion = Math.max(maxVersion, nextVersion);
            log.info("结构快照已更新：{}（{} 列，version {}）", table.name(), columns.size(), nextVersion);
        }

        prune(present);
        admJdbc.update("UPDATE adm_state SET snapshot_version = ? WHERE id = 1", maxVersion);
        return snapshot();
    }

    public SnapshotView snapshot() {
        List<EntitySnapshotEntity> entities = entityMapper.selectList(
                Wrappers.<EntitySnapshotEntity>lambdaQuery().orderByAsc(EntitySnapshotEntity::getEntity));
        List<SnapshotView.Entity> views = new ArrayList<>(entities.size());
        long version = 0;
        for (EntitySnapshotEntity entity : entities) {
            version = Math.max(version, entity.getVersion() == null ? 0 : entity.getVersion());
            List<SnapshotView.Field> fields = fieldsOf(entity.getEntity()).stream()
                    .map(f -> new SnapshotView.Field(f.getField(), f.getDataType(), f.getSemantic(),
                            isTrue(f.getNullable()), isTrue(f.getUniqueFlag()), isTrue(f.getSensitive()),
                            f.getVisibility(), f.getOrdinal() == null ? 0 : f.getOrdinal()))
                    .toList();
            views.add(new SnapshotView.Entity(entity.getEntity(), entity.getDisplayName(), entity.getOwnerField(),
                    entity.getFieldCount() == null ? fields.size() : entity.getFieldCount(),
                    entity.getVersion() == null ? 0 : entity.getVersion(), fields));
        }
        return new SnapshotView(version, views.size(), views);
    }

    // ------------------------------------------------------------------ 内部

    private List<TableMeta> readTables() {
        return bizJdbc.query(SQL_TABLES, (rs, rowNum) -> new TableMeta(rs.getString(1), rs.getString(2)));
    }

    private List<ColumnMeta> readColumns(String table) {
        return bizJdbc.query(SQL_COLUMNS, (rs, rowNum) -> new ColumnMeta(
                rs.getString("COLUMN_NAME"),
                rs.getString("DATA_TYPE").toUpperCase(),
                blankTo(rs.getString("COLUMN_COMMENT"), rs.getString("COLUMN_NAME")),
                "YES".equalsIgnoreCase(rs.getString("IS_NULLABLE")),
                isUniqueKey(rs.getString("COLUMN_KEY")),
                rs.getInt("ORDINAL_POSITION")), table);
    }

    private boolean isUniqueKey(String columnKey) {
        // PRI 与 UNI 都算唯一：主键天然唯一，不能因为 key 类型不同就漏掉
        return "PRI".equalsIgnoreCase(columnKey) || "UNI".equalsIgnoreCase(columnKey);
    }

    private boolean needsRewrite(EntitySnapshotEntity existing, List<FieldSnapshotEntity> existingFields,
                                 List<ColumnMeta> columns) {
        if (existing == null || existing.getFieldCount() == null || existing.getFieldCount() != columns.size()) {
            return true;
        }
        Map<String, FieldSnapshotEntity> byField = index(existingFields);
        for (ColumnMeta column : columns) {
            FieldSnapshotEntity field = byField.get(column.name());
            if (field == null
                    || !Objects.equals(field.getDataType(), column.dataType())
                    || !Objects.equals(field.getSemantic(), column.semantic())
                    || isTrue(field.getNullable()) != column.nullable()
                    || isTrue(field.getUniqueFlag()) != column.unique()
                    || !Objects.equals(field.getOrdinal(), column.ordinal())) {
                return true;
            }
        }
        return false;
    }

    private void rewriteFields(String table, List<ColumnMeta> columns, List<FieldSnapshotEntity> existingFields) {
        Map<String, FieldSnapshotEntity> previous = index(existingFields);
        // 整表重建比逐列比对简单，且快照本来就是派生物、可丢弃
        fieldMapper.delete(Wrappers.<FieldSnapshotEntity>lambdaQuery().eq(FieldSnapshotEntity::getEntity, table));

        for (ColumnMeta column : columns) {
            FieldSnapshotEntity row = new FieldSnapshotEntity();
            row.setEntity(table);
            row.setField(column.name());
            row.setDataType(column.dataType());
            row.setSemantic(column.semantic());
            row.setNullable(column.nullable() ? 1 : 0);
            row.setUniqueFlag(column.unique() ? 1 : 0);
            row.setOrdinal(column.ordinal());

            // 敏感标记与可见策略是「策略」不是「结构」，刷新结构时不能被重置为默认值
            FieldSnapshotEntity old = previous.get(column.name());
            row.setSensitive(old == null ? 0 : old.getSensitive());
            row.setVisibility(old == null ? "PLAIN" : old.getVisibility());

            fieldMapper.insert(row);
        }
    }

    private void prune(Set<String> present) {
        for (EntitySnapshotEntity entity : entityMapper.selectList(null)) {
            if (!present.contains(entity.getEntity())) {
                log.info("表已不存在，清除其结构快照：{}", entity.getEntity());
                entityMapper.deleteById(entity.getEntity());
                fieldMapper.delete(Wrappers.<FieldSnapshotEntity>lambdaQuery()
                        .eq(FieldSnapshotEntity::getEntity, entity.getEntity()));
            }
        }
    }

    private List<FieldSnapshotEntity> fieldsOf(String entity) {
        return fieldMapper.selectList(Wrappers.<FieldSnapshotEntity>lambdaQuery()
                .eq(FieldSnapshotEntity::getEntity, entity)
                .orderByAsc(FieldSnapshotEntity::getOrdinal));
    }

    private Map<String, FieldSnapshotEntity> index(List<FieldSnapshotEntity> fields) {
        Map<String, FieldSnapshotEntity> map = new HashMap<>();
        for (FieldSnapshotEntity field : fields) {
            map.put(field.getField(), field);
        }
        return map;
    }

    private boolean isTrue(Integer flag) {
        return flag != null && flag == 1;
    }

    private String blankTo(String value, String fallback) {
        return value == null || value.isBlank() ? fallback : value;
    }

    private record TableMeta(String name, String comment) {
    }

    private record ColumnMeta(String name, String dataType, String semantic, boolean nullable,
                              boolean unique, int ordinal) {
    }
}
