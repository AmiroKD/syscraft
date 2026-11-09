# spring-skeleton

Java 固定骨架（`platform/` 层），31 个类，手写一次，随每个版本目录整目录复制。

- 包根：`com.generated.platform`
- 依赖只有七个：web / validation / actuator / `mybatis-plus-spring-boot3-starter` /
  `sa-token-spring-boot3-starter` + `sa-token-jwt` / `caffeine` / `mysql-connector-j`
- 业务代码不在这里：`generated/` 由模板渲染，见《代码生成器设计》

类清单见 `../../output/平台骨架接口清单-v1.0.md`。

本目录不参与生成；新增端点需改骨架并升级版本（`代码生成器设计` 10 节已列为局限）。

## 本地启动

```bash
# 1. 构建（Maven 不在 PATH，用绝对路径）
~/.m2/wrapper/dists/apache-maven-3.9.12/57e1dc81/bin/mvn -q package -DskipTests

# 2. 导出凭证。db.env 里是普通 shell 变量，Java 只读进程环境，少了 set -a 会以空口令连接
set -a; source ~/.adaptive/db.env; set +a

# 3. 启动。SERVER__PORT 是 IDE 注入的端口，会覆盖 yml 里的 8080，显式指定更稳
env -u SERVER__PORT java -jar target/syscraft-skeleton-0.1.0.jar --server.port=8080
```

## 验收

```bash
curl -s localhost:8080/actuator/health

TOKEN=$(curl -s -X POST localhost:8080/api/auth/login -H 'Content-Type: application/json' \
  -d "{\"username\":\"$ADMIN_USER\",\"password\":\"$ADMIN_INIT_PWD\"}" \
  | python3 -c 'import sys,json;print(json.load(sys.stdin)["data"]["token"])')

curl -s localhost:8080/api/auth/me -H "Authorization: Bearer $TOKEN"
```

当前只连 `adm` 库；`biz` 数据源要等第一张业务表随模板生成后才需要，届时两个 DataSource 都要显式声明并指定 `@Primary`。

## 权限码探针（第 3 步验收用）

真实业务 Controller 要等模板生成才出现，所以第 3 步先用 `PermissionProbeController` 证明矩阵生效。
它只在 `probe` profile 下加载，正常启动不会注册这两个端点：

```bash
env -u SERVER__PORT java -jar target/syscraft-skeleton-0.1.0.jar \
  --server.port=8080 --spring.profiles.active=probe

# 同一端点，有授权 200、无授权 403
curl -s -w ' %{http_code}\n' localhost:8080/api/probe/order/read -H "Authorization: Bearer $TOKEN"
```

第 4 步真实 Controller 落地后删除 `com.generated.platform.probe` 整个包。


