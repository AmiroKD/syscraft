package com.generated.platform.security;

import com.baomidou.mybatisplus.core.toolkit.Wrappers;
import com.generated.platform.admin.entity.EntitySnapshotEntity;
import com.generated.platform.admin.entity.RoleCapabilityEntity;
import com.generated.platform.admin.entity.RoleEntity;
import com.generated.platform.admin.entity.TableGrantEntity;
import com.generated.platform.admin.entity.UserEntity;
import com.generated.platform.admin.mapper.EntitySnapshotMapper;
import com.generated.platform.admin.mapper.RoleCapabilityMapper;
import com.generated.platform.admin.mapper.RoleMapper;
import com.generated.platform.admin.mapper.TableGrantMapper;
import com.generated.platform.admin.mapper.UserMapper;
import com.github.benmanes.caffeine.cache.Cache;
import com.github.benmanes.caffeine.cache.Caffeine;
import org.springframework.stereotype.Component;

import java.time.Duration;
import java.util.HashMap;
import java.util.HashSet;
import java.util.List;
import java.util.Map;
import java.util.Set;
import java.util.TreeSet;

/**
 * 权限码判定矩阵（《权限与安全设计说明 v0.5》4.6）。
 *
 * <pre>
 * 权限码集合(user) = ⋃ 对每个有效授权 (entity, 档位)：
 *                      { biz:{entity}:{op} | op ∈ 档位允许的操作
 *                                          且 角色具备 op 对应的能力 }
 * </pre>
 *
 * <p>三条不能动的约束：</p>
 * <ol>
 *   <li><b>只来自数据库，只由上面的规则推导。</b>不接受通配符，不从令牌里读——
 *       令牌只带用户 id 与权限版本，角色随时可被改。</li>
 *   <li><b>超管不走授权表。</b>按快照里的全部实体 × 全部操作显式展开，
 *       所以「新增一张表」必须显式刷新快照，不会出现「超管自动拥有未来所有权限」的隐性放大。</li>
 *   <li><b>失败即拒绝。</b>用户不存在、已停用、角色缺失、查询异常，一律返回空集合。</li>
 * </ol>
 */
@Component
public class PermissionCodeResolver {

    /**
     * 超管角色标识。硬编码的理由与 DelegationGuard 排除 CREATE_TABLE 一致：
     * 这条不该被人从配置里改掉，改了就等于把「生成仅超管」这条约束变成可配置项。
     */
    private static final String SUPER_ADMIN_CODE = "SUPER_ADMIN";

    private static final List<String> ALL_OPS = List.of("read", "insert", "update", "delete");

    /** 档位 → 允许的操作（权限 4.6 判定矩阵第一列） */
    private static final Map<String, List<String>> OPS_BY_ACCESS = Map.of(
            "READ", List.of("read"),
            "READ_WRITE", List.of("read", "insert", "update"),
            "FULL", List.of("read", "insert", "update", "delete"));

    /** 档位强弱：取并集时用数值比大小，不比较字符串 */
    private static final Map<String, Integer> RANK_BY_ACCESS = Map.of(
            "READ", 1, "READ_WRITE", 2, "FULL", 3);

    /** 操作 → 该操作需要的角色能力（矩阵第二列） */
    private static final Map<String, String> CAPABILITY_BY_OP = Map.of(
            "read", "QUERY",
            "insert", "INSERT",
            "update", "UPDATE",
            "delete", "DELETE");

    /** TTL 只兜底，主失效靠 perm_version 比对（权限 4.9） */
    private static final Duration TTL = Duration.ofSeconds(60);

    private final UserMapper userMapper;
    private final RoleMapper roleMapper;
    private final RoleCapabilityMapper roleCapabilityMapper;
    private final TableGrantMapper tableGrantMapper;
    private final EntitySnapshotMapper entitySnapshotMapper;

    private final Cache<Long, CachedCodes> cache = Caffeine.newBuilder()
            .maximumSize(10_000)
            .expireAfterWrite(TTL)
            .build();

    public PermissionCodeResolver(UserMapper userMapper, RoleMapper roleMapper,
                                  RoleCapabilityMapper roleCapabilityMapper,
                                  TableGrantMapper tableGrantMapper,
                                  EntitySnapshotMapper entitySnapshotMapper) {
        this.userMapper = userMapper;
        this.roleMapper = roleMapper;
        this.roleCapabilityMapper = roleCapabilityMapper;
        this.tableGrantMapper = tableGrantMapper;
        this.entitySnapshotMapper = entitySnapshotMapper;
    }

    /**
     * 解析某用户的权限码集合。空集合表示「一个业务操作都不许」，不是「不限制」。
     */
    public List<String> resolve(long userId) {
        UserEntity user = userMapper.selectById(userId);
        if (user == null || user.getEnabled() == null || user.getEnabled() == 0) {
            return List.of();
        }
        int permVersion = user.getPermVersion() == null ? 0 : user.getPermVersion();

        CachedCodes hit = cache.getIfPresent(userId);
        if (hit != null && hit.permVersion() == permVersion) {
            return hit.codes();
        }

        List<String> codes = compute(user);
        cache.put(userId, new CachedCodes(permVersion, codes));
        return codes;
    }

    /** 角色/授权一变就要让缓存立刻失效；外部改库时靠 permVersion 自增触发重算 */
    public void invalidate(long userId) {
        cache.invalidate(userId);
    }

    private List<String> compute(UserEntity user) {
        if (user.getRoleId() == null) {
            return List.of();
        }
        RoleEntity role = roleMapper.selectById(user.getRoleId());
        if (role == null) {
            return List.of();
        }
        Set<String> capabilities = capabilitiesOf(role.getId());
        if (capabilities.isEmpty()) {
            return List.of();
        }
        if (SUPER_ADMIN_CODE.equals(role.getCode())) {
            return expandWholeSnapshot(capabilities);
        }
        return expandGrants(user, role, capabilities);
    }

    /** 超管：快照里的全部实体 × 全部操作，不查授权表 */
    private List<String> expandWholeSnapshot(Set<String> capabilities) {
        Set<String> codes = new TreeSet<>();
        List<EntitySnapshotEntity> entities = entitySnapshotMapper.selectList(null);
        for (EntitySnapshotEntity entity : entities) {
            for (String op : ALL_OPS) {
                if (capabilities.contains(CAPABILITY_BY_OP.get(op))) {
                    codes.add(permissionCode(entity.getEntity(), op));
                }
            }
        }
        return List.copyOf(codes);
    }

    /** 其余角色：有效授权按实体取最强档位，再与角色能力求交 */
    private List<String> expandGrants(UserEntity user, RoleEntity role, Set<String> capabilities) {
        List<TableGrantEntity> grants = tableGrantMapper.selectList(Wrappers.<TableGrantEntity>query()
                .and(w -> w.eq("subject_type", "ROLE").eq("subject_id", role.getId()))
                .or(w -> w.eq("subject_type", "USER").eq("subject_id", user.getId())));

        // 取并集而非「用户级覆盖角色级」：后者会让「单独收窄某个人」变成不可能（权限 4.4）
        Map<String, Integer> bestRankByEntity = new HashMap<>();
        for (TableGrantEntity grant : grants) {
            Integer rank = RANK_BY_ACCESS.get(grant.getAccess());
            if (rank == null || grant.getEntity() == null) {
                continue;
            }
            bestRankByEntity.merge(grant.getEntity(), rank, Math::max);
        }

        Set<String> codes = new TreeSet<>();
        for (Map.Entry<String, Integer> entry : bestRankByEntity.entrySet()) {
            for (String op : opsOfRank(entry.getValue())) {
                if (capabilities.contains(CAPABILITY_BY_OP.get(op))) {
                    codes.add(permissionCode(entry.getKey(), op));
                }
            }
        }
        return List.copyOf(codes);
    }

    private Set<String> capabilitiesOf(Long roleId) {
        List<RoleCapabilityEntity> rows = roleCapabilityMapper.selectList(
                Wrappers.<RoleCapabilityEntity>lambdaQuery().eq(RoleCapabilityEntity::getRoleId, roleId));
        Set<String> capabilities = new HashSet<>();
        for (RoleCapabilityEntity row : rows) {
            if (row.getCapability() != null) {
                capabilities.add(row.getCapability());
            }
        }
        return capabilities;
    }

    private List<String> opsOfRank(int rank) {
        for (Map.Entry<String, Integer> entry : RANK_BY_ACCESS.entrySet()) {
            if (entry.getValue() == rank) {
                return OPS_BY_ACCESS.get(entry.getKey());
            }
        }
        return List.of();
    }

    private String permissionCode(String entity, String op) {
        return "biz:" + entity + ":" + op;
    }

    /** 缓存项带版本号：版本一变，这一项自然失效，不需要谁去主动清 */
    private record CachedCodes(int permVersion, List<String> codes) {
    }
}
