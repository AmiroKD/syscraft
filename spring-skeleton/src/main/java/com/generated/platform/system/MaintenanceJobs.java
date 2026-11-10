package com.generated.platform.system;

import com.baomidou.mybatisplus.core.toolkit.Wrappers;
import com.generated.platform.admin.entity.AuditEntity;
import com.generated.platform.admin.entity.LoginAttemptEntity;
import com.generated.platform.admin.entity.TableGrantEntity;
import com.generated.platform.admin.mapper.AuditMapper;
import com.generated.platform.admin.mapper.LoginAttemptMapper;
import com.generated.platform.admin.mapper.TableGrantMapper;
import com.generated.platform.security.PermVersionService;
import com.generated.platform.security.SessionService;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.scheduling.annotation.Scheduled;
import org.springframework.stereotype.Component;

import java.time.Duration;
import java.time.LocalDateTime;
import java.util.HashSet;
import java.util.List;
import java.util.Set;

/**
 * 平台维护任务（底座扩展 v0.1）。三件事，全部可以安全重跑：
 *
 * <ol>
 *   <li><b>过期授权清扫</b>：到点即删并 bump 主体版本。删除动作记一条系统审计
 *       （actor 为 NULL 与生成/定时动作同口径）；即使清扫没跑，
 *       {@code PermissionCodeResolver} 也会实时过滤过期授权，权限不会多——
 *       清扫只是把「逻辑失效」变成「物理回收」，恢复授权列表的整洁。</li>
 *   <li><b>会话登记清理</b>：吊销或失联超 7 天的行删除（{@link SessionService#cleanup()}）。</li>
 *   <li><b>登录尝试清理</b>：30 天前的尝试行删除，防爆破取证表无限增长。</li>
 * </ol>
 */
@Component
public class MaintenanceJobs {

    private static final Logger log = LoggerFactory.getLogger(MaintenanceJobs.class);

    private final TableGrantMapper grantMapper;
    private final AuditMapper auditMapper;
    private final LoginAttemptMapper loginAttemptMapper;
    private final SessionService sessionService;
    private final PermVersionService permVersionService;

    public MaintenanceJobs(TableGrantMapper grantMapper, AuditMapper auditMapper,
                           LoginAttemptMapper loginAttemptMapper, SessionService sessionService,
                           PermVersionService permVersionService) {
        this.grantMapper = grantMapper;
        this.auditMapper = auditMapper;
        this.loginAttemptMapper = loginAttemptMapper;
        this.sessionService = sessionService;
        this.permVersionService = permVersionService;
    }

    /** 每分钟扫一次过期授权。权限正确性不依赖本任务（解析层已过滤），只依赖它回收行 */
    @Scheduled(fixedDelay = 60_000, initialDelay = 30_000)
    public void sweepExpiredGrants() {
        LocalDateTime now = LocalDateTime.now();
        List<TableGrantEntity> expired = grantMapper.selectList(Wrappers.<TableGrantEntity>lambdaQuery()
                .isNotNull(TableGrantEntity::getExpiresAt)
                .le(TableGrantEntity::getExpiresAt, now));
        if (expired.isEmpty()) {
            return;
        }

        Set<String> bumpedSubjects = new HashSet<>();
        StringBuilder ids = new StringBuilder();
        for (TableGrantEntity grant : expired) {
            grantMapper.deleteById(grant.getId());
            String subject = grant.getSubjectType() + "#" + grant.getSubjectId();
            if (bumpedSubjects.add(subject)) {
                permVersionService.bumpSubject(grant.getSubjectType(), grant.getSubjectId());
            }
            ids.append(grant.getId()).append(',');
        }

        String detail = "{\"expired\":" + expired.size()
                + ",\"subjects\":" + bumpedSubjects.stream()
                .map(s -> "\"" + s + "\"").toList() + "}";
        AuditEntity audit = new AuditEntity();
        audit.setActorId(null);          // 系统动作，与生成/定时同口径
        audit.setAction("GRANT_EXPIRE");
        audit.setEntity(null);
        audit.setTargetId(ids.length() > 64 ? ids.substring(0, 63) : ids.toString());
        audit.setImpactRows(expired.size());
        audit.setResult("SUCCESS");
        audit.setDetailJson(detail);
        try {
            auditMapper.insert(audit);
        } catch (RuntimeException e) {
            log.warn("过期授权审计写入失败（清扫已完成 {} 条）", expired.size(), e);
        }
        log.info("过期授权已回收：{} 条，涉及主体 {}", expired.size(), bumpedSubjects);
    }

    /** 每天一次：会话登记与登录尝试的保鲜清理 */
    @Scheduled(fixedDelay = 86_400_000, initialDelay = 300_000) // 24h / 5min（注解值须为编译期常量）
    public void cleanupDaily() {
        int sessions = sessionService.cleanup();
        LocalDateTime deadline = LocalDateTime.now().minus(Duration.ofDays(30));
        int attempts = loginAttemptMapper.delete(Wrappers.<LoginAttemptEntity>lambdaQuery()
                .le(LoginAttemptEntity::getCreatedAt, deadline));
        if (sessions > 0 || attempts > 0) {
            log.info("维护清理：会话登记 {} 行，登录尝试 {} 行", sessions, attempts);
        }
    }
}
