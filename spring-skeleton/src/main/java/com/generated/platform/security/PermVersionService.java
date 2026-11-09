package com.generated.platform.security;

import com.generated.platform.admin.entity.UserEntity;
import com.generated.platform.admin.mapper.UserMapper;
import com.github.benmanes.caffeine.cache.Cache;
import com.github.benmanes.caffeine.cache.Caffeine;
import org.springframework.stereotype.Service;

import java.time.Duration;

/**
 * 权限版本号（权限 4.9）。
 *
 * <p>每个请求都要比对「令牌里的版本」与「库里的版本」，若每次都查库，20 QPS 就是 20 次多余查询。
 * 这里把它缓存在内存，TTL 60 秒兜底；变更走 {@link #invalidate(long)} 立即失效。</p>
 *
 * <p><b>何时必须 invalidate：</b>改用户角色、改角色能力、停用用户、改密。漏一处就会有
 * 最长 60 秒的越权窗口——权限 4.9 明确点过，这也是「把递增封在一个方法里」的理由。</p>
 */
@Service
public class PermVersionService {

    private static final Duration TTL = Duration.ofSeconds(60);

    private final UserMapper userMapper;
    private final Cache<Long, Integer> cache = Caffeine.newBuilder()
            .maximumSize(10_000)
            .expireAfterWrite(TTL)
            .build();

    public PermVersionService(UserMapper userMapper) {
        this.userMapper = userMapper;
    }

    /**
     * 当前版本号。用户不存在返回 -1：它永远不会等于令牌里的版本，
     * 于是「用户被删了」自然走到「令牌失效」，不需要额外分支。
     */
    public int current(long userId) {
        Integer cached = cache.getIfPresent(userId);
        if (cached != null) {
            return cached;
        }
        UserEntity user = userMapper.selectById(userId);
        int version = (user == null || user.getPermVersion() == null) ? -1 : user.getPermVersion();
        cache.put(userId, version);
        return version;
    }

    /** 单个用户变更后调用 */
    public void invalidate(long userId) {
        cache.invalidate(userId);
    }

    /** 角色级变更（改能力、删角色）影响一批人，且人数不多，整体清掉最省事也最不会漏 */
    public void invalidateAll() {
        cache.invalidateAll();
    }
}
