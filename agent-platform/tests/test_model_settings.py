"""LLM 接入配置：URL/Key/模型校验、落盘、掩码、权限与连通性测试。"""

from __future__ import annotations

import json
import stat

import pytest
from fastapi.testclient import TestClient

import syscraft.app as app_module
import syscraft.llm_settings as llm_settings
from syscraft import config


@pytest.fixture()
def llm_file(tmp_path, monkeypatch):
    """独立配置文件 + 固定的环境默认值，避免用例受本机 db.env 影响。"""
    path = tmp_path / "llm-settings.json"
    monkeypatch.setenv("LLM_SETTINGS_PATH", str(path))
    monkeypatch.setattr(config, "LLM_BASE_URL", "https://env.example.com/v1")
    monkeypatch.setattr(config, "LLM_API_KEY", "env-key-1234567890")
    monkeypatch.setattr(config, "LLM_MODEL", "env-model")
    yield path


@pytest.fixture()
def client(llm_file):
    return TestClient(app_module.app)


def login_as(monkeypatch, role: str = "SUPER_ADMIN"):
    async def fake_me(token: str) -> dict:
        return {"id": 1, "username": "admin", "roleCode": role,
                "roleName": "超级管理员" if role == "SUPER_ADMIN" else "管理员"}

    monkeypatch.setattr(app_module._client, "me", fake_me)


# ---------------------------------------------------------------- 纯函数

def test_base_url_normalization():
    assert llm_settings.normalize_base_url("https://api.example.com/v1/") == "https://api.example.com/v1"
    assert (llm_settings.normalize_base_url("https://api.example.com/v1/chat/completions")
            == "https://api.example.com/v1")
    with pytest.raises(llm_settings.LlmConfigError):
        llm_settings.normalize_base_url("file:///etc/passwd")
    with pytest.raises(llm_settings.LlmConfigError):
        llm_settings.normalize_base_url("https://user:pass@example.com/v1")


def test_model_name_validation():
    assert llm_settings.normalize_model(" deepseek-flash ") == "deepseek-flash"
    assert llm_settings.normalize_model("qwen3:8b") == "qwen3:8b"
    with pytest.raises(llm_settings.LlmConfigError):
        llm_settings.normalize_model("bad model name")


def test_snapshot_masks_key(llm_file):
    snapshot = llm_settings.snapshot()
    assert snapshot["apiKeySet"] is True
    assert snapshot["apiKeyMasked"].endswith("7890")
    assert "env-key-1234567890" not in json.dumps(snapshot, ensure_ascii=False)


# ---------------------------------------------------------------- 保存与恢复

def test_set_config_persists_and_load_recovers(llm_file):
    result = llm_settings.set_config("https://custom.example.com/v1/", "sk-custom-abcdefgh", "custom-model")
    assert result["baseUrl"] == "https://custom.example.com/v1"
    assert result["model"] == "custom-model"
    assert config.LLM_API_KEY == "sk-custom-abcdefgh"

    saved = json.loads(llm_file.read_text(encoding="utf-8"))
    assert saved == {
        "baseUrl": "https://custom.example.com/v1",
        "apiKey": "sk-custom-abcdefgh",
        "model": "custom-model",
        "updatedAt": saved["updatedAt"],
    }
    # 密钥文件只给当前用户读写
    assert stat.S_IMODE(llm_file.stat().st_mode) == 0o600

    # 模拟重启：环境变量恢复后 load() 读回文件
    config.LLM_BASE_URL = "https://env.example.com/v1"
    config.LLM_API_KEY = "env-key-1234567890"
    config.LLM_MODEL = "env-model"
    llm_settings.load()
    assert (config.LLM_BASE_URL, config.LLM_API_KEY, config.LLM_MODEL) == (
        "https://custom.example.com/v1", "sk-custom-abcdefgh", "custom-model")


def test_blank_key_keeps_existing_when_url_unchanged(llm_file):
    llm_settings.set_config("https://custom.example.com/v1", "sk-custom-abcdefgh", "m1")
    result = llm_settings.set_config("https://custom.example.com/v1", "", "m2")
    assert result["model"] == "m2"
    assert config.LLM_API_KEY == "sk-custom-abcdefgh"


def test_changing_url_requires_new_key(llm_file):
    llm_settings.set_config("https://custom.example.com/v1", "sk-custom-abcdefgh", "m1")
    with pytest.raises(llm_settings.LlmConfigError):
        llm_settings.set_config("https://other.example.com/v1", "", "m1")


def test_corrupt_file_falls_back_to_env(llm_file):
    llm_file.write_text("{ not json", encoding="utf-8")
    config.LLM_BASE_URL = "https://env.example.com/v1"
    config.LLM_MODEL = "env-model"
    llm_settings.load()
    assert config.LLM_BASE_URL == "https://env.example.com/v1"
    assert config.LLM_MODEL == "env-model"


# ---------------------------------------------------------------- API

def test_get_llm_settings_requires_token(client):
    assert client.get("/api/settings/llm").status_code == 401


def test_get_llm_settings_is_public_only(client, monkeypatch):
    """读取接口不能把服务端 URL/Key/模型回传给页面——它们由用户自己填在浏览器。"""
    login_as(monkeypatch)
    response = client.get("/api/settings/llm", headers={"Authorization": "Bearer t-llm"})
    assert response.status_code == 200
    assert "env-key-1234567890" not in response.text
    data = response.json()["data"]
    assert data["configured"] is True
    for key in ("baseUrl", "model", "apiKeyMasked", "apiKeySet", "source", "updatedAt", "presets"):
        assert key not in data


def test_put_llm_settings_updates_globally(client, monkeypatch, llm_file):
    login_as(monkeypatch)
    response = client.put("/api/settings/llm",
                          headers={"Authorization": "Bearer t-llm"},
                          json={"baseUrl": "https://custom.example.com/v1",
                                "apiKey": "sk-custom-abcdefgh",
                                "model": "custom-model"})
    assert response.status_code == 200
    assert "sk-custom-abcdefgh" not in response.text
    data = response.json()["data"]
    assert data["baseUrl"] == "https://custom.example.com/v1"
    assert data["model"] == "custom-model"
    assert config.LLM_MODEL == "custom-model"
    assert json.loads(llm_file.read_text(encoding="utf-8"))["model"] == "custom-model"


def test_put_llm_settings_rejects_invalid_model(client, monkeypatch):
    login_as(monkeypatch)
    response = client.put("/api/settings/llm",
                          headers={"Authorization": "Bearer t-llm"},
                          json={"baseUrl": "https://custom.example.com/v1",
                                "apiKey": "sk-custom-abcdefgh",
                                "model": "bad model"})
    assert response.status_code == 400


def test_put_llm_settings_requires_admin(client, monkeypatch):
    login_as(monkeypatch, role="USER")
    response = client.put("/api/settings/llm",
                          headers={"Authorization": "Bearer t-llm"},
                          json={"baseUrl": "https://custom.example.com/v1",
                                "apiKey": "sk-custom-abcdefgh",
                                "model": "custom-model"})
    assert response.status_code == 403


def test_test_connection_endpoint(client, monkeypatch):
    login_as(monkeypatch)

    async def fake_probe(base_url, api_key, model):
        return {"ok": True, "latencyMs": 12, "model": model,
                "message": "连接成功 · 12 ms", "reply": "pong"}

    monkeypatch.setattr(llm_settings, "test_connection", fake_probe)
    response = client.post("/api/settings/llm/test",
                           headers={"Authorization": "Bearer t-llm"},
                           json={"baseUrl": "https://custom.example.com/v1",
                                 "apiKey": "sk-custom-abcdefgh",
                                 "model": "custom-model"})
    assert response.status_code == 200
    data = response.json()["data"]
    assert data["ok"] is True and data["model"] == "custom-model"
