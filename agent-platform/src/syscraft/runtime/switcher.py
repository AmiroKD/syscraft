"""版本切换与回滚（运行时 5 节）：停当前产物 → 起指定版本 → 登记状态。

回滚的语义是**切换运行版本**：业务数据保留在 biz 库不动，切回旧版本后
旧版本的接口面对的是新表结构——所以回滚只对「结构兼容的版本」安全，
这是明示的边界，不是遗漏（运行时 5.4）。

Spring 管状态（adm_state.current_version），平台管进程——状态与进程
归属单一，两边不会各记一份「现在跑的是哪个版本」。
"""

from __future__ import annotations

import os
import subprocess
import time
from pathlib import Path

import httpx

from syscraft import config

HEALTH_TIMEOUT_SECONDS = 90
_jar_process: subprocess.Popen | None = None


class SwitchError(Exception):
    pass


def list_versions() -> list[dict]:
    """workspace/versions 下的全部版本；built=jar 已构建，current=正在运行。"""
    current = _current_version()
    result = []
    versions_dir = config.PROJECT_ROOT / "workspace/versions"
    if not versions_dir.exists():
        return result
    for path in sorted(versions_dir.iterdir()):
        if not path.is_dir():
            continue
        jar = path / "target/syscraft-skeleton-0.1.0.jar"
        result.append({"version": path.name, "built": jar.exists(),
                       "current": current == path.name})
    return result


def switch(version: str) -> dict:
    """切换运行版本。成功返回 {version, pid}；任何失败抛 SwitchError。"""
    global _jar_process
    from syscraft.runtime.pipeline import _healthy, _port_busy

    version = (version or "").strip()
    if not version.replace("v", "").replace("r", "").isdigit() or "/" in version or ".." in version:
        raise SwitchError(f"非法版本号：{version!r}")
    version_dir = config.PROJECT_ROOT / "workspace/versions" / version
    jar = version_dir / "target/syscraft-skeleton-0.1.0.jar"
    if not jar.exists():
        raise SwitchError(f"版本 {version} 没有构建产物（jar 不存在），请先运行流水线")

    # ① 停当前产物：只匹配自家 jar（运行组 stop_app 的语义）
    if _port_busy(config.SPRING_PORT) or _healthy(config.SPRING_PORT):
        subprocess.run(["pkill", "-f", "syscraft-skeleton-0.1.0.jar"], capture_output=True)
        deadline = time.time() + 15
        while time.time() < deadline and (_port_busy(config.SPRING_PORT) or _healthy(config.SPRING_PORT)):
            time.sleep(0.5)
        if _port_busy(config.SPRING_PORT):
            raise SwitchError("旧产物停止失败，端口仍被占用")

    # ② 起新版本
    log_file = config.PROJECT_ROOT / "workspace/logs" / f"{version}-app.log"
    log_file.parent.mkdir(parents=True, exist_ok=True)
    _jar_process = subprocess.Popen(
        ["java", "-jar", str(jar), f"--server.port={config.SPRING_PORT}"],
        stdout=open(log_file, "w"), stderr=subprocess.STDOUT, env={**os.environ},
        # 守护化：切过去的版本不随平台重启而陪葬（产物生命周期归平台管，但不归平台进程组）
        start_new_session=True)
    time.sleep(3)
    if _jar_process.poll() is not None:
        raise SwitchError(f"版本 {version} 启动即退出，见 {log_file}")

    deadline = time.time() + HEALTH_TIMEOUT_SECONDS
    while time.time() < deadline:
        if _jar_process.poll() is not None:
            raise SwitchError(f"版本 {version} 进程提前退出，见 {log_file}")
        if _healthy(config.SPRING_PORT):
            break
        time.sleep(1)
    else:
        raise SwitchError(f"版本 {version} 健康检查超时")

    # ③ 登记状态（Spring 管状态；失败不回滚进程——状态可以重登，进程别折腾）
    try:
        token = _admin_token()
        httpx.put(f"{config.SPRING_BASE_URL}/api/system/state",
                  headers={"Authorization": f"Bearer {token}"},
                  json={"currentVersion": version}, timeout=5)
    except httpx.HTTPError:
        pass  # 状态登记失败不阻塞切换：下次 GET state 仍显示旧值，可重试
    return {"version": version, "pid": _jar_process.pid}


def current_version() -> str | None:
    """当前登记的运行版本（供流水线失败回滚等外部使用）。
    产物不在线时读不到 → None，调用方自行判断是否需要回滚。"""
    return _current_version()


def _current_version() -> str | None:
    """从产物读当前版本；state 端点要登录（401 的 data 是 null），带 admin 令牌。
    产物不在线/任何异常都返回 None——清单里只是没有「正在运行」的标记。"""
    try:
        response = httpx.get(f"{config.SPRING_BASE_URL}/api/system/state",
                             headers={"Authorization": f"Bearer {_admin_token()}"}, timeout=3)
        data = response.json().get("data") or {}
        return data.get("currentVersion")
    except (httpx.HTTPError, ValueError, KeyError, TypeError, SwitchError):
        # TypeError：登录失败时 data 为 None 再取 token 会炸（实测踩过）——
        # 探针读不到状态就当 None，调用方自行决定是否回滚，不能让流水线因此挂掉
        return None


def _admin_token() -> str:
    response = httpx.post(f"{config.SPRING_BASE_URL}/api/auth/login", json={
        "username": os.environ.get("ADMIN_USER", ""),
        "password": os.environ.get("ADMIN_INIT_PWD", ""),
    }, timeout=5)
    body = response.json()
    token = (body.get("data") or {}).get("token")
    if not token:
        raise SwitchError(f"超管登录失败：{body.get('msg')}")
    return token
