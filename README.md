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
└── spring-skeleton/       # Java 固定骨架：platform/ 31 个类
    └── src/main/java/com/generated/platform/
```

设计与交接文档在仓库外：`../output/`。**开工先读 `../output/开发起手指南-v1.0.md`。**

## 快速开始

```bash
cd agent-platform
uv venv --python 3.12 && source .venv/bin/activate
uv pip install -e ".[dev]"
```

数据库凭证不在仓库里：

```bash
source ~/.adaptive/db.env
```

## 约定

- 分支 `main`，一个任务一次 commit，message 写「做了什么 + 验收结果」
- 不提交 `workspace/`、`.venv/`、`target/`、`.env`
- `platform/` 与 `templates/` 只读，由路径白名单硬拦
- 先生成新文件、校验通过、再单独删旧文件
