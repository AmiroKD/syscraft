# SysCraft（语构）

把一段自然语言需求，变成一套**可编译、可运行、自带鉴权的 Spring 业务系统**。

- **语构**：用语言构建系统。
- **SysCraft**：craft a system by talking。

平台（Python）常驻，负责理解需求、生成代码、编译部署；Spring 是被它生成出来的**产物**，可整体重建、可回滚。

## 仓库结构

```
code/
├── agent-platform/        # Python 平台：对话、解析、生成、运行时
│   ├── src/syscraft/
│   │   ├── conversation/  # 双模式状态机、SSE 事件流
│   │   ├── requirement/   # 需求解析、澄清追问、ModelDiff
│   │   ├── codegen/       # 模板渲染 + RepairLoop
│   │   ├── runtime/       # 编译、启停、验收、回滚
│   │   ├── tools/         # 四组工具、路径沙箱
│   │   └── prompt/
│   ├── templates/         # 代码模板（修复循环不可写）
│   ├── tests/
│   └── workspace/         # 运行期产物（不进版本库）
└── spring-skeleton/       # Java 固定骨架：platform/ 96 个类
    └── src/main/java/com/generated/platform/
```

设计与交接文档在仓库外：`../output/`。**开工先读 `../output/开发起手指南-v1.0.md`。**

## 快速开始

```bash
cd agent-platform
uv venv --python 3.12
uv pip install -r requirements.lock --python .venv/bin/python
```

依赖分两份：`requirements.txt` 只写意图（允许升级），`requirements.lock` 锁死补丁版本
（答辩/复现按这份装，行为才可重复）。

数据库凭证不在仓库里：

```bash
set -a; source ~/.adaptive/db.env; set +a   # 少了 set -a，Java 读不到 APP_PWD
```

## 一次完整生成（一条命令）

```bash
cd agent-platform

# 一条命令：停旧产物 → 渲染 → 建表 → 编译（失败进修复循环）→ 启动
#           → 模型快照 → 默认授权 → 自动验收 → 版本登记 → 失败自动回滚
PYTHONPATH=src .venv/bin/python -m syscraft.runtime.pipeline --model models/trade_order.v1.json
```

生成器拒绝覆盖已存在的版本目录：换 `model_version` 或先手工删除。

只跑渲染（不碰数据库、不编译）：

```bash
PYTHONPATH=src .venv/bin/python -m syscraft.codegen.generator --model models/trade_order.v1.json
```

Maven 按 `mvn`（PATH）→ `MAVEN_HOME` → `~/.m2/wrapper` 三级定位，不依赖某台机器的哈希目录。

## 冒烟自检

```bash
./scripts/smoke.sh                  # 健康 → 登录 → 版本清单 → 一次只读查询
./scripts/smoke.sh "查一下活动"      # 换一句只读问题
```

退出码非 0 即冒烟失败，答辩前跑一遍比人工点页面可靠。

## 对话链路（§5 第 5 步，最窄）

```bash
# 终端 A：产物侧（已编译好的 v1）
cd agent-platform/workspace/versions/v1
env -u SERVER__PORT java -jar target/syscraft-skeleton-0.1.0.jar --server.port=8080

# 终端 B：平台侧
cd agent-platform
PYTHONPATH=src .venv/bin/python -m uvicorn syscraft.app:app --host 127.0.0.1 --port 8090

# 登录拿令牌，再发一句话
set -a; source ~/.adaptive/db.env; set +a
TOKEN=$(curl -s -X POST localhost:8090/api/auth/login -H 'Content-Type: application/json' \
  -d "{\"username\":\"$ADMIN_USER\",\"password\":\"$ADMIN_INIT_PWD\"}" \
  | python3 -c 'import sys,json;print(json.load(sys.stdin)["data"]["token"])')

curl -s -X POST localhost:8090/api/chat -H "Authorization: Bearer $TOKEN" \
  -H 'Content-Type: application/json' -d '{"message":"查订单"}'
```

当前范围：意图解析是**规则版**（认实体、「字段 是 值」、条数），只支持查询；写入类请求会明确拒绝而不是静默当成查询。
LLM 版实现同一个 `IntentResolver` 协议即可替换，编排层不动。


## 约定

- 分支 `main`，一个任务一次 commit，message 写「做了什么 + 验收结果」
- 不提交 `workspace/`、`.venv/`、`target/`、`.env`
- `platform/` 与 `templates/` 只读，由路径白名单硬拦
- 先生成新文件、校验通过、再单独删旧文件
