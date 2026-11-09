package com.generated.platform.system;

import com.baomidou.mybatisplus.core.toolkit.Wrappers;
import com.generated.platform.admin.entity.EntitySnapshotEntity;
import com.generated.platform.admin.mapper.EntitySnapshotMapper;
import com.generated.platform.admin.entity.StateEntity;
import com.generated.platform.admin.mapper.StateMapper;
import org.springframework.beans.factory.annotation.Qualifier;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;

import java.util.List;
import java.util.Set;

/**
 * 平台状态与启动自检（契约 2.2 的 state / readiness）。
 *
 * <p>readiness 只查平台自己的库和表，<b>不探业务表</b>——biz 里有什么是快照的事，
 * 启动自检探业务表会把「还没生成任何东西」的正常状态误报成故障。</p>
 */
@Service
public class SysStateService {

    /** 13 张 adm 表：12 张来自元数据 4.8，外加本次补的 adm_model_config（契约 2.2 的落库位置） */
    private static final Set<String> SYS_TABLES = Set.of(
            "adm_user", "adm_role", "adm_role_capability", "adm_table_grant", "adm_column_policy",
            "adm_entity_snapshot", "adm_field_snapshot", "adm_model_snapshot", "adm_request_dedup",
            "adm_confirm_token", "adm_audit", "adm_state", "adm_model_config");

    private final StateMapper stateMapper;
    private final EntitySnapshotMapper entitySnapshotMapper;
    private final JdbcTemplate admJdbc;

    public SysStateService(StateMapper stateMapper, EntitySnapshotMapper entitySnapshotMapper,
                           @Qualifier("admJdbcTemplate") JdbcTemplate admJdbc) {
        this.stateMapper = stateMapper;
        this.entitySnapshotMapper = entitySnapshotMapper;
        this.admJdbc = admJdbc;
    }

    public record SysState(boolean initialized, String currentVersion, long snapshotVersion,
                           long modelVersion, int entityCount) {
    }

    public record Readiness(String db, String sysTables, String snapshot) {
    }

    public SysState state() {
        StateEntity state = requireState();
        Long entities = entitySnapshotMapper.selectCount(null);
        return new SysState(
                state.getInitialized() != null && state.getInitialized() == 1,
                state.getCurrentVersion(),
                state.getSnapshotVersion() == null ? 0 : state.getSnapshotVersion(),
                state.getModelVersion() == null ? 0 : state.getModelVersion(),
                entities == null ? 0 : entities.intValue());
    }

    /** 仅超管（契约 2.2）：删光业务表后回到未初始化，用于重新走一遍首次引导（基线 B5） */
    public void setInitialized(boolean initialized) {
        StateEntity state = requireState();
        state.setInitialized(initialized ? 1 : 0);
        stateMapper.updateById(state);
    }

    public Readiness readiness() {
        String db = "ok";
        try {
            admJdbc.queryForObject("SELECT 1", Integer.class);
        } catch (RuntimeException e) {
            db = "down";
        }

        List<String> names = admJdbc.query(
                "SELECT TABLE_NAME FROM information_schema.TABLES WHERE TABLE_SCHEMA = DATABASE()",
                (rs, n) -> rs.getString(1));
        long found = names == null ? 0 : names.stream().filter(SYS_TABLES::contains).count();
        String sysTables = found == SYS_TABLES.size() ? "ok" : "missing:" + (SYS_TABLES.size() - found);

        Long entities = entitySnapshotMapper.selectCount(Wrappers.<EntitySnapshotEntity>lambdaQuery());
        String snapshot = entities != null && entities > 0 ? "ok" : "empty";
        return new Readiness(db, sysTables, snapshot);
    }

    private StateEntity requireState() {
        StateEntity state = stateMapper.selectById(1);
        if (state == null) {
            throw new IllegalStateException("adm_state 缺少单行记录，请重放 init.sql");
        }
        return state;
    }
}
