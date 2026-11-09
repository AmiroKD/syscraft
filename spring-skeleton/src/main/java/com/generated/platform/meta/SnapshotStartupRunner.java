package com.generated.platform.meta;

import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.boot.ApplicationArguments;
import org.springframework.boot.ApplicationRunner;
import org.springframework.stereotype.Component;

/**
 * 启动时刷新结构快照。
 *
 * <p>失败只告警不阻断：快照是派生物，坏了系统还能跑业务，最多是权限码少一批；
 * 但产物起不来就什么都没了。这条取舍是刻意的。</p>
 */
@Component
public class SnapshotStartupRunner implements ApplicationRunner {

    private static final Logger log = LoggerFactory.getLogger(SnapshotStartupRunner.class);

    private final SnapshotService snapshotService;

    public SnapshotStartupRunner(SnapshotService snapshotService) {
        this.snapshotService = snapshotService;
    }

    @Override
    public void run(ApplicationArguments args) {
        try {
            SnapshotView view = snapshotService.refresh();
            log.info("结构快照就绪：{} 个实体，版本 {}", view.entityCount(), view.version());
        } catch (Exception e) {
            log.warn("结构快照刷新失败，不影响启动：{}", e.getMessage());
        }
    }
}
