package com.generated.platform.meta;

import java.util.List;

/**
 * 结构快照的对外形态（《平台与产物接口契约》2.4 /api/meta/snapshot）。
 *
 * <p>快照是<b>派生物</b>：可以整份删除再重建，删了不影响系统运行。
 * 所以它的 version 只在结构真变了的时候才自增，重启不会平白涨版本。</p>
 */
public record SnapshotView(long version, int entityCount, List<Entity> entities) {

    public record Entity(String entity, String displayName, String ownerField, int fieldCount,
                         long version, List<Field> fields) {
    }

    public record Field(String field, String dataType, String semantic, boolean nullable,
                        boolean unique, boolean sensitive, String visibility, int ordinal) {
    }
}
