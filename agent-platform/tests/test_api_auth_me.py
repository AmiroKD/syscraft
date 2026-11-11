"""平台 /api/auth/me：浏览器唯一入口的身份转发。

前端进主界面第一件事就是取身份；这个端点缺失时登录后停在登录页报「请求失败」，
而 curl 直测 /api/chat 全绿——只有走浏览器的链路才会暴露，所以单独钉一条用例。
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

import syscraft.app as app_module
from syscraft.tools.spring_client import SpringError


@pytest.fixture
def client():
    return TestClient(app_module.app)


def test_me_without_token_is_401(client):
    response = client.get("/api/auth/me")
    assert response.status_code == 401
    assert response.json()["msg"] == "未提供令牌"


def test_me_forwards_identity(client, monkeypatch):
    """平台不解析令牌：原样转发给产物，把 data 解包成前端认的形态。"""
    seen: list[str] = []

    async def fake_me(token: str) -> dict:
        seen.append(token)
        return {"id": 1, "username": "admin", "roleCode": "SUPER_ADMIN",
                "roleName": "超级管理员"}

    monkeypatch.setattr(app_module._client, "me", fake_me)
    response = client.get("/api/auth/me", headers={"Authorization": "Bearer t-123"})
    assert response.status_code == 200
    body = response.json()
    assert body["code"] == 0
    assert body["data"]["username"] == "admin"
    assert seen == ["t-123"]


def test_me_when_artifact_offline_is_503(client, monkeypatch):
    """产物停机且无登录缓存：503 而不是 500，前端据此提示「服务不可达」。"""
    async def offline(token: str) -> dict:
        raise SpringError(503, None, "产物服务不可达")

    monkeypatch.setattr(app_module._client, "me", offline)
    response = client.get("/api/auth/me", headers={"Authorization": "Bearer unknown"})
    assert response.status_code == 503
