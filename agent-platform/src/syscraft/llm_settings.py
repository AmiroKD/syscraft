"""LLM 接入配置（设置面板）：Base URL / API Key / 模型名，运行时可改。

为什么是平台级而不是每用户：四个 LLM 入口（对话 Agent、意图解析、需求抽取、
修复循环）都是进程内单例，统一从 `config.LLM_*` 读取；做每用户配置得把 model
一路透传到每次 HTTP 调用，收益不抵复杂度。答辩/演示场景是单管理员，平台级
配置语义最直接，界面上也如实写「全局生效」。

安全边界：
- API Key 只落本机 `~/.adaptive/llm-settings.json`（权限 600，与 db.env 同目录，
  不进 OneDrive/仓库）；读取接口只返回「是否已配置」，不回传 URL / Key / 模型名，
  表单默认空白、由用户填写并缓存在自己的浏览器；
- 写临时文件再 replace，避免进程中断留下半截 JSON；写失败回滚内存值，
  避免「界面显示新配置、实际还在用旧配置」；
- Base URL 只接受 http/https，禁止带账号密码/查询串，避免把配置变成任意请求。
"""

from __future__ import annotations

import json
import os
import re
import threading
import time
from datetime import datetime
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

import httpx

from syscraft import config

_MODEL_RE = re.compile(r"^[A-Za-z0-9._:\-/@]{1,100}$")
_lock = threading.Lock()
_source = "env"
_updated_at = ""


class LlmConfigError(Exception):
    """配置值不合法（端点返回 400）。"""


class LlmSettingsError(Exception):
    """配置已改内存但落盘失败（端点返回 500，已回滚）。"""


def _settings_path() -> Path:
    override = os.getenv("LLM_SETTINGS_PATH") or os.getenv("MODEL_SETTINGS_PATH")
    if override:
        return Path(override)
    # 与 db.env 同目录：项目在 OneDrive 里，密钥不跟着云同步走
    return Path.home() / ".adaptive" / "llm-settings.json"


def mask_key(key: str) -> str:
    if not key:
        return ""
    if len(key) <= 8:
        return "••••"
    return "••••" + key[-4:]


def normalize_base_url(value: str) -> str:
    """只接受 http/https；统一去掉尾部斜杠与误填的 /chat/completions。"""
    raw = (value or "").strip()
    if not raw:
        raise LlmConfigError("请填写 API Base URL")
    parsed = urlsplit(raw)
    if parsed.scheme not in ("http", "https"):
        raise LlmConfigError("Base URL 只支持 http/https")
    if not parsed.netloc:
        raise LlmConfigError("Base URL 缺少域名")
    if parsed.username or parsed.password:
        raise LlmConfigError("Base URL 不能包含账号密码")
    if parsed.query or parsed.fragment:
        raise LlmConfigError("Base URL 不能带查询串或锚点")
    path = parsed.path.rstrip("/")
    if path.endswith("/chat/completions"):
        path = path[: -len("/chat/completions")].rstrip("/")
    return urlunsplit((parsed.scheme, parsed.netloc, path, "", ""))


def normalize_model(value: str) -> str:
    model = (value or "").strip()
    if not model:
        raise LlmConfigError("请填写模型名")
    if not _MODEL_RE.match(model):
        raise LlmConfigError("模型名只能包含字母、数字、点、下划线、短横线、冒号、斜杠和 @")
    return model


def snapshot() -> dict:
    """管理端保存后的完整快照（含掩码）。只给 PUT 响应用，不下发给普通读取。"""
    return {
        "baseUrl": config.LLM_BASE_URL,
        "model": config.LLM_MODEL,
        "apiKeySet": bool(config.LLM_API_KEY),
        "apiKeyMasked": mask_key(config.LLM_API_KEY),
        "source": _source,
        "updatedAt": _updated_at,
        "global": True,
    }


def public_snapshot() -> dict:
    """给前端的公开状态：只说明平台是否已有配置。

    刻意不返回 Base URL / Key / 模型名：这些是用户自己填、缓存在浏览器的内容，
    页面不该从服务端回填（否则看起来像写死的默认值）。
    """
    return {
        "configured": bool(config.LLM_API_KEY),
        "global": True,
    }


def load() -> dict:
    """启动时读取上次保存的配置；文件缺失/损坏/非法都静默回退环境变量。"""
    global _source, _updated_at
    path = _settings_path()
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        _source = "env"
        _updated_at = ""
        return snapshot()

    if not isinstance(raw, dict):
        _source = "env"
        return snapshot()

    base_url = str(raw.get("baseUrl") or "").strip()
    model = str(raw.get("model") or "").strip()
    api_key = raw.get("apiKey")
    if base_url:
        try:
            config.LLM_BASE_URL = normalize_base_url(base_url)
        except LlmConfigError:
            pass
    if model:
        try:
            config.LLM_MODEL = normalize_model(model)
        except LlmConfigError:
            pass
    if isinstance(api_key, str) and api_key.strip():
        config.LLM_API_KEY = api_key.strip()
    _source = "file"
    _updated_at = str(raw.get("updatedAt") or "")
    return snapshot()


def _save(base_url: str, api_key: str, model: str) -> None:
    path = _settings_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {"baseUrl": base_url, "apiKey": api_key, "model": model,
               "updatedAt": datetime.now().isoformat(timespec="seconds")}
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    try:
        os.chmod(tmp, 0o600)
    except OSError:
        # Key 是明文落盘，0600 是唯一防线：POSIX 上收不紧权限就必须回滚报错，
        # 不能让文件按默认 umask 变成组/全员可读还报告「保存成功」。
        # Windows 没有这套权限位，降级跳过是唯一合理的语境
        if os.name != "nt":
            tmp.unlink(missing_ok=True)
            raise LlmSettingsError("无法将模型配置收权为仅本人可读（chmod 0600 失败），已取消保存")
    tmp.replace(path)


def set_config(base_url: str, api_key: str | None, model: str) -> dict:
    """保存并立即生效。api_key 为空 = 沿用已保存的密钥。

    更换 Base URL 时必须重新填 Key：旧 Key 发到新服务既可能泄露，也可能
    直接把新服务打成 401，错误现场很难解释。
    """
    global _source, _updated_at
    base = normalize_base_url(base_url)
    name = normalize_model(model)
    key = (api_key or "").strip()
    if not key:
        if not config.LLM_API_KEY:
            raise LlmConfigError("首次配置需要填写 API Key")
        if base != config.LLM_BASE_URL:
            raise LlmConfigError("更换服务地址时请重新填写 API Key")
        key = config.LLM_API_KEY

    with _lock:
        previous = (config.LLM_BASE_URL, config.LLM_API_KEY, config.LLM_MODEL)
        config.LLM_BASE_URL = base
        config.LLM_API_KEY = key
        config.LLM_MODEL = name
        try:
            _save(base, key, name)
        except OSError as error:
            config.LLM_BASE_URL, config.LLM_API_KEY, config.LLM_MODEL = previous
            raise LlmSettingsError(f"配置写入失败：{error}") from error
        _source = "file"
        _updated_at = datetime.now().isoformat(timespec="seconds")
    return snapshot()


def _http_message(status: int, body: str) -> str:
    if status == 401:
        return "鉴权失败（401），请检查 API Key"
    if status == 403:
        return "没有访问权限（403），请检查 API Key 的权限"
    if status == 404:
        return "接口不存在（404），请检查 Base URL 是否填到 /v1 为止"
    if status == 429:
        return "请求过于频繁（429），请稍后再试"
    detail = (body or "").strip().replace("\n", " ")[:160]
    return f"模型服务返回 HTTP {status}：{detail}" if detail else f"模型服务返回 HTTP {status}"


async def test_connection(base_url: str, api_key: str | None, model: str) -> dict:
    """用最小补全验证 URL / Key / 模型是否真的可用。"""
    base = normalize_base_url(base_url)
    name = normalize_model(model)
    key = (api_key or "").strip() or config.LLM_API_KEY
    if not key:
        return {"ok": False, "message": "请填写 API Key"}

    started = time.perf_counter()
    try:
        async with httpx.AsyncClient(timeout=45) as client:
            response = await client.post(
                f"{base}/chat/completions",
                headers={"Authorization": f"Bearer {key}"},
                json={"model": name, "messages": [{"role": "user", "content": "ping"}],
                      "max_tokens": 8, "temperature": 0},
            )
    except httpx.TimeoutException:
        return {"ok": False, "message": "连接超时（45 秒），请检查 Base URL 或网络"}
    except httpx.HTTPError as error:
        detail = str(error).strip()
        name = type(error).__name__
        reason = f"{name}: {detail}" if detail else name
        return {"ok": False,
                "message": f"无法连接模型服务（{reason}），请检查 Base URL、网络或服务是否在线"}

    elapsed = int((time.perf_counter() - started) * 1000)
    if response.status_code != 200:
        return {"ok": False, "status": response.status_code, "latencyMs": elapsed,
                "message": _http_message(response.status_code, response.text)}
    try:
        body = response.json()
        message = body["choices"][0]["message"]
    except (ValueError, KeyError, IndexError, TypeError):
        return {"ok": False, "status": response.status_code, "latencyMs": elapsed,
                "message": "模型返回的不是 OpenAI 兼容格式"}
    content = message.get("content")
    if isinstance(content, list):
        content = "".join(str(part.get("text", "")) for part in content if isinstance(part, dict))
    return {"ok": True, "latencyMs": elapsed, "model": name,
            "message": f"连接成功 · {elapsed} ms",
            "reply": str(content or "")[:60]}
