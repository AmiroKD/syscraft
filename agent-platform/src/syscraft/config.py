"""平台侧配置。全部来自环境变量，默认值只用于本机开发。"""

from __future__ import annotations

import os
from pathlib import Path

# 平台根目录（agent-platform/），用于定位 models/ 与 workspace/
PROJECT_ROOT = Path(__file__).resolve().parents[2]

# 产物侧地址。平台是唯一入口，浏览器不直连 Spring（《接口契约》1 节）
SPRING_BASE_URL = os.getenv("SPRING_BASE_URL", "http://127.0.0.1:8080")
# 产物端口单独给出生成流程用（停旧产物、健康探测都只认端口）
SPRING_PORT = int(os.getenv("SPRING_PORT", "8080"))

# 产物侧调用超时：生成物接口都是单表短查询，5 秒足够；超时说明产物卡住了
SPRING_TIMEOUT_SECONDS = float(os.getenv("SPRING_TIMEOUT_SECONDS", "5"))

# 平台自身监听端口（不与 Spring 的 8080 冲突）
PLATFORM_PORT = int(os.getenv("PLATFORM_PORT", "8090"))

MODELS_DIR = PROJECT_ROOT / "models"

# ---- 会话存储（chat 库）----
# Python 侧唯一可写的库：与 adm（权限）和 biz（业务）分开，账号也只授权 chat.*，
# 这样「平台不写权限库」不靠自觉，而是靠数据库权限兜住。
CHAT_DB_HOST = os.getenv("CHAT_DB_HOST", "127.0.0.1")
CHAT_DB_PORT = int(os.getenv("CHAT_DB_PORT", "3306"))
CHAT_DB_NAME = os.getenv("CHAT_DB_NAME", "chat")
CHAT_DB_USER = os.getenv("CHAT_DB_USER", "chat")
CHAT_DB_PASSWORD = os.getenv("CHAT_DB_PASSWORD", "")

# ---- 产物库（biz）连接：流水线建表/迁移用（凭证走 MCP_USER / MCP_PWD）----
# host/port 进配置而不是硬编码 127.0.0.1:3306——换机器/换端口不该改代码
BIZ_DB_HOST = os.getenv("BIZ_DB_HOST", CHAT_DB_HOST)
BIZ_DB_PORT = int(os.getenv("BIZ_DB_PORT", str(CHAT_DB_PORT)))

# ---- LLM 接入（来源 db.env，与数据库凭证同一文件、同一权限）----
# 没配 LLM_API_KEY 时，对话解析退回规则版 IntentResolver
LLM_BASE_URL = os.getenv("LLM_BASE_URL", "https://api.openai.com/v1").rstrip("/")
LLM_API_KEY = os.getenv("LLM_API_KEY", "")
LLM_MODEL = os.getenv("LLM_MODEL", "")
LLM_TIMEOUT_SECONDS = float(os.getenv("LLM_TIMEOUT_SECONDS", "30"))
LLM_MAX_TOKENS = int(os.getenv("LLM_MAX_TOKENS", "1024"))
