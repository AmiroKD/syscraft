-- ============================================================================
-- SysCraft（语构）· adm 库初始化脚本
--
-- 依据：《权限与安全设计说明 v0.5》4.5 默认能力矩阵、4.8 adm 库表清单
--       《平台骨架接口清单 v1.0》5 核销三态机、9.1 审计两段式
--       《元数据模型与能力目录 v0.4》3.1 / 3.2 快照字段
--
-- 做什么：12 张 adm_ 表 + 三个内置角色 + 默认能力矩阵 + 超管账号 + 单行状态记录
-- 不做什么：不建 biz 库业务表（由生成流程负责）；不写快照数据（首次生成成功后导出）
--
-- 幂等：建表一律 IF NOT EXISTS，种子一律 INSERT IGNORE。
--       重复执行不报错、不重复插入；且不会覆盖已改过的口令与运行时状态
--       （IGNORE 而非 ON DUPLICATE KEY UPDATE，就是为了让第二次执行是纯无操作）。
--       MySQL 的 DDL 隐式提交，脚本不使用事务，幂等靠语句本身保证，不靠回滚。
--
-- 执行（app 账号；口令只从 ~/.adaptive/db.env 取，不写死在这里）：
--   source ~/.adaptive/db.env
--   mysql -h127.0.0.1 -P3306 -u"$APP_USER" -p"$APP_PWD" < init.sql
--
-- 超管初始口令：见 ~/.adaptive/db.env 的 ADMIN_INIT_PWD，明文不入库、不入本文件。
--   下面 adm_user 中只存其 BCrypt(cost=10) 派生值，must_change_password = 1 强制首登改密。
--   该哈希由 `htpasswd -bnBC 10 "" "$ADMIN_INIT_PWD"` 生成；输出前缀为 $2y$，
--   此处改写为 $2a$——两者算法相同（仅非 ASCII 口令的符号扩展差异，本口令为 ASCII），
--   $2a$ 同时被 Spring Security BCryptPasswordEncoder 与 jBCrypt 接受。
--
-- 注 1（本机实测）：SENSITIVE 是本机 MySQL 26.10 的**保留字**
--   （information_schema.KEYWORDS 中 RESERVED=1），不加反引号建表直接 1064。
--   字段名沿用《权限》4.8 的 sensitive 不改名，因此建表与查询一律写 `sensitive`；
--   平台侧实体映射需 @TableField("`sensitive`")，否则 MyBatis-Plus 生成的 SQL 会报错。
--
-- 注 2：布尔列用 TINYINT 而非 TINYINT(1)——本机 MySQL 已弃用整数显示宽度，
--   TINYINT(1) 每次建表刷一条 1681 警告。MyBatis 的布尔映射不依赖显示宽度。
-- ============================================================================

SET NAMES utf8mb4;

-- 权限库物理名是 adm 而不是 sys：MySQL 自带 sys 库（100+ 视图）无法占用。
CREATE DATABASE IF NOT EXISTS adm
    DEFAULT CHARACTER SET utf8mb4
    COLLATE utf8mb4_0900_ai_ci;

USE adm;

-- ============================================================================
-- 一、12 张表
-- ============================================================================

-- 1. adm_role 角色
CREATE TABLE IF NOT EXISTS adm_role (
    id          BIGINT UNSIGNED NOT NULL AUTO_INCREMENT COMMENT '角色主键',
    code        VARCHAR(32)     NOT NULL                COMMENT '角色标识，程序内引用；内置角色不可改',
    name        VARCHAR(64)     NOT NULL                COMMENT '角色显示名',
    level       INT             NOT NULL                COMMENT '层级，越大越高；委托约束比较该值，同层不可互相修改',
    builtin     TINYINT         NOT NULL DEFAULT 0      COMMENT '1=内置角色，不可删除；超管角色任何人不可修改',
    created_at  DATETIME        NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at  DATETIME        NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    PRIMARY KEY (id),
    UNIQUE KEY uk_role_code (code),
    KEY idx_role_level (level)
) ENGINE = InnoDB DEFAULT CHARSET = utf8mb4 COLLATE = utf8mb4_0900_ai_ci COMMENT = '角色';

-- 2. adm_user 账号
CREATE TABLE IF NOT EXISTS adm_user (
    id                   BIGINT UNSIGNED NOT NULL AUTO_INCREMENT COMMENT '用户主键',
    username             VARCHAR(64)     NOT NULL                COMMENT '登录名，全局唯一',
    password_hash        VARCHAR(100)    NOT NULL                COMMENT '口令的 BCrypt 派生值；明文不入库、不进代码',
    role_id              BIGINT UNSIGNED NOT NULL                COMMENT '所属角色；单值，不支持多角色叠加（权限 9 节局限 3）',
    enabled              TINYINT         NOT NULL DEFAULT 1      COMMENT '停用即拒绝登录，并使 perm_version 自增',
    perm_version         INT             NOT NULL DEFAULT 0      COMMENT '权限版本；改角色/改角色能力/停用用户时自增，令牌版本不符即失效',
    must_change_password TINYINT         NOT NULL DEFAULT 0      COMMENT '1=下次登录必须改密（初始账号、超管重置后）',
    created_by           BIGINT UNSIGNED NULL                    COMMENT '创建人 user_id，系统初始化为 NULL',
    created_at           DATETIME        NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at           DATETIME        NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    PRIMARY KEY (id),
    UNIQUE KEY uk_user_username (username),
    KEY idx_user_role (role_id),
    CONSTRAINT fk_user_role FOREIGN KEY (role_id) REFERENCES adm_role (id) ON DELETE RESTRICT
) ENGINE = InnoDB DEFAULT CHARSET = utf8mb4 COLLATE = utf8mb4_0900_ai_ci COMMENT = '账号';

-- 3. adm_role_capability 角色 × 能力
--    能力种类封闭为九种，用 ENUM 在库层面固化：角色可以取舍，但造不出第十种。
CREATE TABLE IF NOT EXISTS adm_role_capability (
    role_id    BIGINT UNSIGNED NOT NULL COMMENT '角色',
    capability ENUM ('QUERY', 'INSERT', 'UPDATE', 'DELETE', 'GRANT',
                     'MANAGE_USER', 'MANAGE_ROLE', 'CREATE_TABLE', 'ALTER_TABLE')
               NOT NULL COMMENT '九种能力之一；CREATE_TABLE/ALTER_TABLE 为超管专有，接口层拒绝授予',
    created_by BIGINT UNSIGNED NULL,
    created_at DATETIME        NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (role_id, capability),
    KEY idx_capability (capability),
    CONSTRAINT fk_rc_role FOREIGN KEY (role_id) REFERENCES adm_role (id) ON DELETE CASCADE
) ENGINE = InnoDB DEFAULT CHARSET = utf8mb4 COLLATE = utf8mb4_0900_ai_ci COMMENT = '角色能力';

-- 4. adm_table_grant 表级授权
--    主体可以是用户或角色；主键天然保证「一个主体 × 一张表只有一条」，重复授予是覆盖而非追加。
CREATE TABLE IF NOT EXISTS adm_table_grant (
    id           BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
    subject_type ENUM ('USER', 'ROLE') NOT NULL       COMMENT '授权主体类型',
    subject_id   BIGINT UNSIGNED       NOT NULL       COMMENT '主体 id；多态引用，故不建外键',
    entity       VARCHAR(64)           NOT NULL       COMMENT '业务表名，与 adm_entity_snapshot.entity 一致',
    access       ENUM ('READ', 'READ_WRITE', 'FULL') NOT NULL COMMENT '档位：只读 / 读写 / 读写删',
    granted_by   BIGINT UNSIGNED       NULL           COMMENT '授出人 user_id；系统随生成写入时为 NULL',
    created_at   DATETIME              NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at   DATETIME              NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    PRIMARY KEY (id),
    UNIQUE KEY uk_grant_subject_entity (subject_type, subject_id, entity),
    KEY idx_grant_entity (entity)
) ENGINE = InnoDB DEFAULT CHARSET = utf8mb4 COLLATE = utf8mb4_0900_ai_ci COMMENT = '表级授权';

-- 5. adm_column_policy 字段可见策略
CREATE TABLE IF NOT EXISTS adm_column_policy (
    entity     VARCHAR(64) NOT NULL COMMENT '所属实体（业务表名）',
    field      VARCHAR(64) NOT NULL COMMENT '字段名',
    visibility ENUM ('PLAIN', 'MASK', 'HIDDEN') NOT NULL DEFAULT 'PLAIN'
               COMMENT '明文 / 掩码（138****5678）/ 隐藏；隐藏字段不进 VO',
    updated_by BIGINT UNSIGNED NULL,
    created_at DATETIME    NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at DATETIME    NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    PRIMARY KEY (entity, field)
) ENGINE = InnoDB DEFAULT CHARSET = utf8mb4 COLLATE = utf8mb4_0900_ai_ci COMMENT = '字段策略';

-- 6. adm_confirm_token 确认令牌
--    只存令牌哈希；三态机 ISSUED → CLAIMED → CONSUMED，业务失败回退 ISSUED 可重试。
--    核销用一次条件更新抢占：影响行数为 0 即拒绝（409），先抢占再执行，防并发重复提交。
CREATE TABLE IF NOT EXISTS adm_confirm_token (
    token_hash  CHAR(64)        NOT NULL COMMENT '令牌 SHA-256 十六进制；明文令牌只在响应里出现一次',
    fingerprint CHAR(64)        NOT NULL COMMENT 'sha256(操作 + 实体 + 规范化条件 + 影响行数)，条件按字段名字典序',
    op_type     VARCHAR(16)     NOT NULL COMMENT 'INSERT / UPDATE / DELETE / BATCH_INSERT',
    entity      VARCHAR(64)     NOT NULL COMMENT '目标实体',
    impact_rows INT UNSIGNED    NOT NULL DEFAULT 0 COMMENT '预告时的影响行数；执行时比对，不一致即 409',
    issued_by   BIGINT UNSIGNED NOT NULL COMMENT '签发人 user_id',
    expires_at  DATETIME        NOT NULL COMMENT '有效期 5 分钟',
    state       ENUM ('ISSUED', 'CLAIMED', 'CONSUMED') NOT NULL DEFAULT 'ISSUED' COMMENT '核销状态',
    claimed_at  DATETIME        NULL COMMENT '抢占时间',
    used_at     DATETIME        NULL COMMENT '核销完成时间（state=CONSUMED）',
    created_at  DATETIME        NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (token_hash),
    KEY idx_ct_state_expires (state, expires_at),
    KEY idx_ct_issued_by (issued_by, created_at)
) ENGINE = InnoDB DEFAULT CHARSET = utf8mb4 COLLATE = utf8mb4_0900_ai_ci COMMENT = '确认令牌';

-- 7. adm_state 平台单行状态
--    单行表：用 CHECK 把 id 钉死为 1，避免出现第二行导致「哪个才是当前版本」的歧义。
CREATE TABLE IF NOT EXISTS adm_state (
    id               TINYINT UNSIGNED NOT NULL DEFAULT 1 COMMENT '恒为 1',
    initialized      TINYINT          NOT NULL DEFAULT 0 COMMENT '0=仅建库，尚未完成首次生成',
    current_version  VARCHAR(16)      NULL               COMMENT '当前运行的代码版本，如 v3；随回滚一并更新',
    snapshot_version BIGINT UNSIGNED  NOT NULL DEFAULT 0 COMMENT '结构快照版本',
    model_version    BIGINT UNSIGNED  NOT NULL DEFAULT 0 COMMENT '业务模型版本，与代码版本同源',
    updated_at       DATETIME         NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    PRIMARY KEY (id),
    CONSTRAINT ck_state_singleton CHECK (id = 1)
) ENGINE = InnoDB DEFAULT CHARSET = utf8mb4 COLLATE = utf8mb4_0900_ai_ci COMMENT = '平台状态（单行）';

-- 8. adm_model_snapshot 业务模型快照（生成输入的原件，每版本一行）
CREATE TABLE IF NOT EXISTS adm_model_snapshot (
    version    BIGINT UNSIGNED NOT NULL COMMENT '模型版本，与代码版本同源',
    model_json JSON            NOT NULL COMMENT '模型原文：实体、关系、规则、假设、建议角色、接口计划',
    created_by BIGINT UNSIGNED NULL,
    created_at DATETIME        NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (version)
) ENGINE = InnoDB DEFAULT CHARSET = utf8mb4 COLLATE = utf8mb4_0900_ai_ci COMMENT = '业务模型快照';

-- 9. adm_entity_snapshot 实体快照（当前态，每实体一行，可删除重建）
CREATE TABLE IF NOT EXISTS adm_entity_snapshot (
    entity       VARCHAR(64)     NOT NULL COMMENT '实体标识，与业务表名一致',
    display_name VARCHAR(64)     NOT NULL COMMENT '中文名，用于对话渲染',
    owner_field  VARCHAR(64)     NULL     COMMENT '行级归属字段，可空；为空表示该表不做行级隔离',
    field_count  INT UNSIGNED    NOT NULL DEFAULT 0 COMMENT '字段数，便于展示',
    version      BIGINT UNSIGNED NOT NULL DEFAULT 0 COMMENT '该实体结构版本，每次变更自增',
    generated_at DATETIME        NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '最近一次生成时间',
    created_at   DATETIME        NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at   DATETIME        NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    PRIMARY KEY (entity)
) ENGINE = InnoDB DEFAULT CHARSET = utf8mb4 COLLATE = utf8mb4_0900_ai_ci COMMENT = '实体快照';

-- 10. adm_field_snapshot 字段快照（当前态，一次全量读取，不分页）
CREATE TABLE IF NOT EXISTS adm_field_snapshot (
    entity      VARCHAR(64) NOT NULL COMMENT '所属实体',
    field       VARCHAR(64) NOT NULL COMMENT '字段标识',
    data_type   VARCHAR(16) NOT NULL COMMENT '八种数据类型枚举之一',
    semantic    VARCHAR(64) NULL     COMMENT '中文语义',
    nullable    TINYINT NOT NULL DEFAULT 1 COMMENT '可空',
    unique_flag TINYINT NOT NULL DEFAULT 0 COMMENT '唯一',
    `sensitive` TINYINT NOT NULL DEFAULT 0 COMMENT '是否敏感；只能上调，不能下调（保留字，必须反引号，见注 1）',
    visibility  ENUM ('PLAIN', 'MASK', 'HIDDEN') NOT NULL DEFAULT 'PLAIN' COMMENT '明文 / 掩码 / 隐藏',
    ordinal     INT         NOT NULL DEFAULT 0 COMMENT '展示顺序',
    created_at  DATETIME    NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at  DATETIME    NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    PRIMARY KEY (entity, field),
    KEY idx_fs_entity_ordinal (entity, ordinal)
) ENGINE = InnoDB DEFAULT CHARSET = utf8mb4 COLLATE = utf8mb4_0900_ai_ci COMMENT = '字段快照';

-- 11. adm_audit 审计
--     两段式：执行前 INSERT result=INTENT，执行后 UPDATE 为 SUCCESS/FAILED 并回填影响行数。
--     中途崩溃会留下一条永远没有结果的 INTENT——这正是排查「那次操作到底做没做」的证据。
--     无删除接口。
CREATE TABLE IF NOT EXISTS adm_audit (
    id          BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
    actor_id    BIGINT UNSIGNED NULL     COMMENT '操作者 user_id；系统动作（生成、定时）为 NULL',
    action      VARCHAR(64)     NOT NULL COMMENT '动作：查询/新增/修改/删除/授权/生成/编译/回滚等',
    entity      VARCHAR(64)     NULL     COMMENT '目标实体；非业务操作可空',
    target_id   VARCHAR(64)     NULL     COMMENT '目标主键，字符串以兼容复合主键与非自增键',
    impact_rows INT             NULL     COMMENT '影响行数；INTENT 阶段未知',
    result      ENUM ('INTENT', 'SUCCESS', 'FAILED') NOT NULL COMMENT '意图 / 成功 / 失败',
    detail_json JSON            NULL     COMMENT '前后差异、完整 DDL 脚本、错误信息等',
    trace_id    VARCHAR(64)     NULL     COMMENT '对应请求头 X-Agent-Trace，把一次对话的多条请求串成链路',
    created_at  DATETIME        NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at  DATETIME        NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    PRIMARY KEY (id),
    KEY idx_audit_actor_time (actor_id, created_at),
    KEY idx_audit_entity_time (entity, created_at),
    KEY idx_audit_trace (trace_id)
) ENGINE = InnoDB DEFAULT CHARSET = utf8mb4 COLLATE = utf8mb4_0900_ai_ci COMMENT = '审计';

-- 12. adm_request_dedup 请求幂等去重
--      Agent 超时重试时用同一个 requestId 再发一次，直接回放上次响应，避免重复插入。
CREATE TABLE IF NOT EXISTS adm_request_dedup (
    request_id    VARCHAR(64) NOT NULL COMMENT '调用方生成的请求 id',
    response_json JSON        NOT NULL COMMENT '首次执行的响应，重试时原样回放',
    expires_at    DATETIME    NOT NULL COMMENT '过期时间；到期后由清理任务删除，可重新提交',
    created_at    DATETIME    NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (request_id),
    KEY idx_dedup_expires (expires_at)
) ENGINE = InnoDB DEFAULT CHARSET = utf8mb4 COLLATE = utf8mb4_0900_ai_ci COMMENT = '请求去重';

-- ============================================================================
-- 二、内置角色（超管 100 / 管理员 50 / 普通用户 10）
--     用 code 取 id 再灌能力，而不是写死 1/2/3：即使有人手工插过同 code 的角色，
--     能力也会挂到真实 id 上，不会静默挂错。
-- ============================================================================

INSERT IGNORE INTO adm_role (code, name, level, builtin) VALUES
    ('SUPER_ADMIN', '超级管理员', 100, 1),
    ('ADMIN',       '管理员',      50, 1),
    ('USER',        '普通用户',    10, 1);

SET @role_super = (SELECT id FROM adm_role WHERE code = 'SUPER_ADMIN');
SET @role_admin = (SELECT id FROM adm_role WHERE code = 'ADMIN');
SET @role_user  = (SELECT id FROM adm_role WHERE code = 'USER');

-- ============================================================================
-- 三、默认能力矩阵（权限 4.5）
--
--   能力            超管  管理员  普通用户
--   QUERY  查询      ✓     ✓      ✓
--   INSERT 新增      ✓     ✓      ✓
--   UPDATE 修改      ✓     ✓      ✓
--   DELETE 删除      ✓     ✓      ✗（需显式授予）
--   GRANT  授权      ✓     ✓      ✗
--   MANAGE_USER      ✓     ✓      ✗
--   MANAGE_ROLE      ✓     ✓(仅下级) ✗
--   CREATE_TABLE     ✓     ✗      ✗（超管专有，接口层拒绝授予）
--   ALTER_TABLE      ✓     ✗      ✗（同上）
--                   9 项  7 项   3 项
-- ============================================================================

INSERT IGNORE INTO adm_role_capability (role_id, capability) VALUES
    -- 超管：九种全给
    (@role_super, 'QUERY'),
    (@role_super, 'INSERT'),
    (@role_super, 'UPDATE'),
    (@role_super, 'DELETE'),
    (@role_super, 'GRANT'),
    (@role_super, 'MANAGE_USER'),
    (@role_super, 'MANAGE_ROLE'),
    (@role_super, 'CREATE_TABLE'),
    (@role_super, 'ALTER_TABLE'),
    -- 管理员：七项，不含建表与改表
    (@role_admin, 'QUERY'),
    (@role_admin, 'INSERT'),
    (@role_admin, 'UPDATE'),
    (@role_admin, 'DELETE'),
    (@role_admin, 'GRANT'),
    (@role_admin, 'MANAGE_USER'),
    (@role_admin, 'MANAGE_ROLE'),
    -- 普通用户：三项，只读改之外只能新增；删除需授予
    (@role_user, 'QUERY'),
    (@role_user, 'INSERT'),
    (@role_user, 'UPDATE');

-- ============================================================================
-- 四、超管账号
--     口令派生值对应的明文在 ~/.adaptive/db.env 的 ADMIN_INIT_PWD（不在此文件）。
--     must_change_password = 1：首登必须改密，改密后 perm_version 自增并作废当前会话。
--     perm_version 从 0 起，与「新建用户即 perm_version = 0」一致。
-- ============================================================================

INSERT IGNORE INTO adm_user
    (username, password_hash, role_id, enabled, perm_version, must_change_password, created_by)
VALUES
    ('admin',
     '$2a$10$EWgqzY7slE/uvady/Xb3neE9xYbk6tYS3hRKjktt.neopgQrYEvs2',
     @role_super, 1, 0, 1, NULL);

-- ============================================================================
-- 五、单行状态
--     initialized = 0：本脚本只把权限体系立起来，系统结构与业务表还没生成。
--     首次生成成功后由运行时把它置 1，并写入 current_version / snapshot_version / model_version。
-- ============================================================================

INSERT IGNORE INTO adm_state (id, initialized, current_version, snapshot_version, model_version)
VALUES (1, 0, NULL, 0, 0);

-- ============================================================================
-- 六、验收查询（只作说明，不自动执行；手工跑这四条即可核对）
--
--   -- ① 表数量应为 12
--   SELECT COUNT(*) AS tables FROM information_schema.TABLES WHERE TABLE_SCHEMA = 'adm';
--
--   -- ② 三角色能力条数应为 9 / 7 / 3
--   SELECT r.code, COUNT(c.capability) AS caps
--     FROM adm_role r LEFT JOIN adm_role_capability c ON c.role_id = r.id
--    WHERE r.code IN ('SUPER_ADMIN','ADMIN','USER')
--    GROUP BY r.code ORDER BY r.level DESC;
--
--   -- ③ 超管账号存在且 must_change_password = 1
--   SELECT u.username, r.code AS role_code, u.must_change_password, u.enabled
--     FROM adm_user u JOIN adm_role r ON r.id = u.role_id WHERE u.username = 'admin';
--
--   -- ④ 超管有权限码、普通用户没有（权限 4.6：码由能力 ∩ 授权推导，这里只看能力面）
--   SELECT r.code,
--          GROUP_CONCAT(c.capability ORDER BY c.capability) AS capabilities
--     FROM adm_role r LEFT JOIN adm_role_capability c ON c.role_id = r.id
--    GROUP BY r.code ORDER BY r.level DESC;
-- ============================================================================
