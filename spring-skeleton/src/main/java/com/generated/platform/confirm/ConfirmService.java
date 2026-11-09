package com.generated.platform.confirm;

import com.baomidou.mybatisplus.core.toolkit.Wrappers;
import com.generated.platform.admin.entity.ConfirmTokenEntity;
import com.generated.platform.admin.mapper.ConfirmTokenMapper;
import com.generated.platform.common.BizException;
import com.generated.platform.common.ErrorCode;
import cn.dev33.satoken.stp.StpUtil;
import org.springframework.stereotype.Service;

import java.nio.charset.StandardCharsets;
import java.security.MessageDigest;
import java.security.SecureRandom;
import java.time.LocalDateTime;
import java.util.Base64;
import java.util.HexFormat;

/**
 * 确认令牌（权限 4.7 / 接口清单 5）。
 *
 * <p>流程：预告（preview）→ 用户确认 → 业务端点内核销（consume）。三态机：
 * ISSUED → CLAIMED → CONSUMED，业务失败回退 ISSUED 可重试。</p>
 *
 * <p>三条不能动的规则：</p>
 * <ol>
 *   <li><b>先抢占再执行。</b>核销用一次条件更新（state='ISSUED' AND expires_at&gt;NOW() AND fingerprint=?），
 *       影响行数为 0 即 409——这才能防并发重复提交；先执行后标记在并发下会执行两次。</li>
 *   <li><b>只存哈希。</b>明文令牌只在预告响应里出现一次，泄露了也只值五分钟。</li>
 *   <li><b>指纹进 WHERE。</b>指纹 = sha256(操作|实体|规范化参数|影响行数)，
 *       「预告删 2 条、执行时传了 3 个 id」这种偷换会被指纹拦下。</li>
 * </ol>
 */
@Service
public class ConfirmService {

    private static final long TTL_MINUTES = 5;

    private final ConfirmTokenMapper tokenMapper;
    private final SecureRandom random = new SecureRandom();

    public ConfirmService(ConfirmTokenMapper tokenMapper) {
        this.tokenMapper = tokenMapper;
    }

    public record Ticket(String token, int impactRows, String summary, LocalDateTime expiresAt) {
    }

    public Ticket preview(String op, String entity, String canonical, int impactRows, String summary) {
        // 顺手清掉过期令牌，省一张定时任务
        tokenMapper.delete(Wrappers.<ConfirmTokenEntity>lambdaQuery()
                .lt(ConfirmTokenEntity::getExpiresAt, LocalDateTime.now()));

        byte[] bytes = new byte[32];
        random.nextBytes(bytes);
        String token = Base64.getUrlEncoder().withoutPadding().encodeToString(bytes);

        LocalDateTime expiresAt = LocalDateTime.now().plusMinutes(TTL_MINUTES);
        ConfirmTokenEntity row = new ConfirmTokenEntity();
        row.setTokenHash(sha256(token));
        row.setFingerprint(fingerprint(op, entity, canonical, impactRows));
        row.setOpType(op);
        row.setEntity(entity);
        row.setImpactRows(impactRows);
        row.setIssuedBy(StpUtil.getLoginIdAsLong());
        row.setExpiresAt(expiresAt);
        row.setState("ISSUED");
        tokenMapper.insert(row);

        return new Ticket(token, impactRows, summary, expiresAt);
    }

    /**
     * 抢占式核销，返回令牌哈希。业务成功后 {@link #markConsumed}，失败 {@link #release} 回退可重试。
     * 失败一律 409：过期、已用、不存在、指纹不匹配对外是同一句话，不给攻击者试探的回执。
     */
    public String consume(String token, String op, String entity, String canonical) {
        if (token == null || token.isBlank()) {
            throw new BizException(ErrorCode.BAD_REQUEST, "该操作需要先获取确认令牌（/api/confirm/preview）");
        }
        ConfirmTokenEntity row = tokenMapper.selectById(sha256(token));
        if (row == null) {
            throw new BizException(ErrorCode.CONFLICT, "确认令牌无效或已过期");
        }
        String expected = fingerprint(op, entity, canonical, row.getImpactRows() == null ? 0 : row.getImpactRows());
        if (!expected.equals(row.getFingerprint())) {
            throw new BizException(ErrorCode.CONFLICT, "确认令牌与本次操作不匹配");
        }

        int claimed = tokenMapper.update(null, Wrappers.<ConfirmTokenEntity>lambdaUpdate()
                .eq(ConfirmTokenEntity::getTokenHash, row.getTokenHash())
                .eq(ConfirmTokenEntity::getState, "ISSUED")
                .gt(ConfirmTokenEntity::getExpiresAt, LocalDateTime.now())
                .eq(ConfirmTokenEntity::getFingerprint, expected)
                .set(ConfirmTokenEntity::getState, "CLAIMED")
                .set(ConfirmTokenEntity::getClaimedAt, LocalDateTime.now()));
        if (claimed == 0) {
            throw new BizException(ErrorCode.CONFLICT, "确认令牌已被使用或已过期");
        }
        return row.getTokenHash();
    }

    public void markConsumed(String tokenHash) {
        tokenMapper.update(null, Wrappers.<ConfirmTokenEntity>lambdaUpdate()
                .eq(ConfirmTokenEntity::getTokenHash, tokenHash)
                .eq(ConfirmTokenEntity::getState, "CLAIMED")
                .set(ConfirmTokenEntity::getState, "CONSUMED")
                .set(ConfirmTokenEntity::getUsedAt, LocalDateTime.now()));
    }

    /** 业务失败回退 ISSUED：用户修正后可拿同一令牌重试，不必再走一遍预告 */
    public void release(String tokenHash) {
        tokenMapper.update(null, Wrappers.<ConfirmTokenEntity>lambdaUpdate()
                .eq(ConfirmTokenEntity::getTokenHash, tokenHash)
                .eq(ConfirmTokenEntity::getState, "CLAIMED")
                .set(ConfirmTokenEntity::getState, "ISSUED")
                .set(ConfirmTokenEntity::getClaimedAt, null));
    }

    /**
     * 指纹。canonical 由调用方给「排序去重后的参数序列化」，同类操作不同参数必然不同指纹。
     */
    public static String fingerprint(String op, String entity, String canonical, int impactRows) {
        return sha256(op + "|" + entity + "|" + canonical + "|impact=" + impactRows);
    }

    private static String sha256(String text) {
        try {
            byte[] digest = MessageDigest.getInstance("SHA-256")
                    .digest(text.getBytes(StandardCharsets.UTF_8));
            return HexFormat.of().formatHex(digest);
        } catch (Exception e) {
            throw new IllegalStateException("SHA-256 不可用", e);
        }
    }
}
