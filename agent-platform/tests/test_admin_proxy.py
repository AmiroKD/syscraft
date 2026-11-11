"""管理员面板转发：平台只守入口，能力校验在产物侧。

对话工具不接管理接口是刻意的边界；补这条通道时同样要守住两件事——
入口收窄到管理员、只转发显式列出的端点。这里逐条钉住，防止后续
「顺手改成通配转发」把对话外的管理面扩大。
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

import syscraft.app as app_module
from syscraft.tools.spring_client import SpringError


@pytest.fixture
def client():
    return TestClient(app_module.app)


def _fake_me(role_code):
    async def fake_me(token: str) -> dict:
        return {"id": 1, "username": "admin", "roleCode": role_code}

    return fake_me


def test_admin_users_without_token_is_401(client):
    assert client.get("/api/admin/users").status_code == 401


def test_admin_users_rejects_regular_user(client, monkeypatch):
    """平台第一道门：普通用户连入口都进不去（即使产物侧还没判能力）。"""
    monkeypatch.setattr(app_module._client, "me", _fake_me("USER"))
    response = client.get("/api/admin/users", headers={"Authorization": "Bearer t-user"})
    assert response.status_code == 403
    assert "管理员" in response.json()["msg"]


def test_admin_users_forwards_token_and_query(client, monkeypatch):
    """转发调用者自己的令牌与查询串；数据由产物侧按 MANAGE_USER 校验后返回。"""
    seen: dict = {}

    async def fake_passthrough(method, path, payload, token, params=None):
        seen.update(method=method, path=path, payload=payload, token=token,
                    params=dict(params or {}))
        return {"rows": [{"id": 2, "username": "zhangsan", "roleCode": "USER",
                          "enabled": True}], "total": 1, "page": 1, "size": 20,
                "truncated": False}

    monkeypatch.setattr(app_module._client, "me", _fake_me("SUPER_ADMIN"))
    monkeypatch.setattr(app_module._client, "passthrough", fake_passthrough)
    response = client.get("/api/admin/users?keyword=zhang&page=1",
                          headers={"Authorization": "Bearer t-admin"})
    assert response.status_code == 200
    assert response.json()["data"]["rows"][0]["username"] == "zhangsan"
    assert seen["method"] == "GET" and seen["path"] == "/api/admin/users"
    assert seen["params"] == {"keyword": "zhang", "page": "1"}
    assert seen["token"] == "t-admin"


def test_admin_create_user_forwards_payload(client, monkeypatch):
    seen: dict = {}

    async def fake_passthrough(method, path, payload, token, params=None):
        seen.update(method=method, path=path, payload=payload)
        return {"id": 9, "username": "zhangsan", "roleCode": "USER", "enabled": True}

    monkeypatch.setattr(app_module._client, "me", _fake_me("ADMIN"))
    monkeypatch.setattr(app_module._client, "passthrough", fake_passthrough)
    response = client.post("/api/admin/users", headers={"Authorization": "Bearer t-admin"},
                           json={"username": "zhangsan", "password": "initial-pw-8",
                                 "roleCode": "USER"})
    assert response.status_code == 200
    assert seen["payload"]["username"] == "zhangsan"


def test_admin_create_user_rejects_non_object_body(client, monkeypatch):
    monkeypatch.setattr(app_module._client, "me", _fake_me("ADMIN"))
    response = client.post("/api/admin/users", headers={"Authorization": "Bearer t-admin"},
                           json=[1, 2])
    assert response.status_code == 400


def test_admin_validation_error_passes_through_as_400(client, monkeypatch):
    """产物侧 400（密码长度等）是可修复错误：原样透传，不是笼统 502。"""
    async def fail(method, path, payload, token, params=None):
        raise SpringError(400, 40001, "长度需在 8-64 之间")

    monkeypatch.setattr(app_module._client, "me", _fake_me("ADMIN"))
    monkeypatch.setattr(app_module._client, "passthrough", fail)
    response = client.post("/api/admin/users", headers={"Authorization": "Bearer t-admin"},
                           json={"username": "zhangsan", "password": "short", "roleCode": "USER"})
    assert response.status_code == 400
    assert response.json()["msg"] == "长度需在 8-64 之间"


def test_admin_reset_password_exposes_one_time_secret(client, monkeypatch):
    """重置密码返回一次性初始口令——这正是它必须走专用界面而非对话的原因。"""
    async def fake_passthrough(method, path, payload, token, params=None):
        assert method == "POST" and path.endswith("/password")
        return {"username": "zhangsan", "initialPassword": "one-time-pw-9"}

    monkeypatch.setattr(app_module._client, "me", _fake_me("SUPER_ADMIN"))
    monkeypatch.setattr(app_module._client, "passthrough", fake_passthrough)
    response = client.post("/api/admin/users/2/password",
                           headers={"Authorization": "Bearer t-admin"})
    assert response.status_code == 200
    assert response.json()["data"]["initialPassword"] == "one-time-pw-9"


def test_admin_revoke_grant_uses_delete(client, monkeypatch):
    seen: dict = {}

    async def fake_passthrough(method, path, payload, token, params=None):
        seen.update(method=method, path=path)
        return None

    monkeypatch.setattr(app_module._client, "me", _fake_me("ADMIN"))
    monkeypatch.setattr(app_module._client, "passthrough", fake_passthrough)
    response = client.delete("/api/admin/grants/7", headers={"Authorization": "Bearer t-admin"})
    assert response.status_code == 200
    assert seen == {"method": "DELETE", "path": "/api/admin/grants/7"}
