"""平台侧配置。全部来自环境变量，默认值只用于本机开发。"""

from __future__ import annotations

import os
from pathlib import Path

# 平台根目录（agent-platform/），用于定位 models/ 与 workspace/
PROJECT_ROOT = Path(__file__).resolve().parents[2]

# 产物侧地址。平台是唯一入口，浏览器不直连 Spring（《接口契约》1 节）
SPRING_BASE_URL = os.getenv("SPRING_BASE_URL", "http://127.0.0.1:8080")

# 产物侧调用超时：生成物接口都是单表短查询，5 秒足够；超时说明产物卡住了
SPRING_TIMEOUT_SECONDS = float(os.getenv("SPRING_TIMEOUT_SECONDS", "5"))

# 平台自身监听端口（不与 Spring 的 8080 冲突）
PLATFORM_PORT = int(os.getenv("PLATFORM_PORT", "8090"))

MODELS_DIR = PROJECT_ROOT / "models"
