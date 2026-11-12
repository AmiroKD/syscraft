# spring-skeleton

Java 固定骨架（`platform/` 层），手写一次，随每个版本目录整目录复制。

- 包根：`com.generated.platform`
- 依赖：web / validation / actuator / `mybatis-plus-spring-boot3-starter` /
  `sa-token-spring-boot3-starter` + `sa-token-jwt` / `caffeine` / `mysql-connector-j`（见 pom.xml，以它为准——这里的数字会过时，类数与依赖数不写死）
- 业务代码不在这里：`generated/` 由模板渲染，见《代码生成器设计》

类清单见 `../../output/平台骨架接口清单-v1.0.md`（清单有统计日期；本 README 不再维护具体数量，避免文档漂移）。

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

## 双数据源

`adm`（权限）与 `biz`（业务）各一个数据源、各一个 `SqlSessionFactory`：

- 扫描入口分别是 `AdmDataSourceConfig` 与 `BizDataSourceConfig`，`PlatformApplication` 不写 `@MapperScan`
- 启动类扫描根是 `com.generated`：生成物在 `com.generated.generated`，用默认扫描根会整批漏掉（现象是全接口 404，且启动不报错）
- 两个库不能共用一个工厂，否则 admin 的 Mapper 会拿 biz 的连接去查 `adm_user`
