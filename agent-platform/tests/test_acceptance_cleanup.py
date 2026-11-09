"""验收中断统一清理（审查 r5 留存项修复）：用例中途失败留下的半截数据，
回合结束必须兜底删掉——演示环境不能被验收垃圾污染。"""

from __future__ import annotations

import json

import httpx
import pytest

from syscraft.runtime.acceptance import AcceptanceRunner

SPEC = {
    "modelVersion": "vTest",
    "accounts": [{"role": "SUPER_ADMIN", "username": "__acc_admin__",
                  "usernameRef": "env:TEST_USER", "passwordRef": "env:TEST_PWD"}],
    "cases": [{
        "id": "crud_book", "title": "图书增删改查", "level": "block",
        "check": {
            "type": "crud", "base": "/api/biz/book", "entity": "book",
            "unique_fields": ["name"],
            "sample": {"name": "__acc_test_book", "author": "张三"},
        },
    }],
}


@pytest.fixture()
def calls():
    return []


@pytest.fixture()
def update_fails():
    return {"on": True}


@pytest.fixture()
def runner(monkeypatch, calls, update_fails):
    monkeypatch.setenv("TEST_PWD", "pwd-123")
    monkeypatch.setenv("TEST_USER", "admin")
    state = {"row": {}}  # 模拟库里那一条记录，update 后要能查回

    def handler(request: httpx.Request) -> httpx.Response:
        path, method = request.url.path, request.method
        body = json.loads(request.content or b"{}")
        calls.append({"method": method, "path": path, "body": body})
        if path == "/api/auth/login":
            return httpx.Response(200, json={"code": 0, "data": {"token": "tok"}})
        if path == "/api/biz/book/insert":
            state["row"] = dict(body)
            return httpx.Response(200, json={"code": 0, "data": 42})
        if path == "/api/biz/book/query":
            rows = [dict(state["row"], id=42)] if state["row"] else []
            return httpx.Response(200, json={"code": 0, "data": {"rows": rows}})
        if path == "/api/biz/book/update":
            if update_fails["on"]:
                return httpx.Response(500, json={"code": 500, "msg": "服务端异常"})
            state["row"].update({k: v for k, v in body.items() if k != "id"})
            return httpx.Response(200, json={"code": 0, "data": 1})
        if path == "/api/confirm/preview":
            return httpx.Response(200, json={"code": 0, "data": {"token": "ctok", "impactRows": 1}})
        if path == "/api/biz/book/delete":
            state["row"] = {}
            return httpx.Response(200, json={"code": 0, "data": 1})
        return httpx.Response(404, json={"code": 404, "msg": "no route"})

    transport = httpx.MockTransport(handler)
    runner = AcceptanceRunner("http://mock", SPEC)
    runner.client = httpx.Client(base_url="http://mock", transport=transport)
    yield runner
    runner.close()


def test_failed_case_leaves_no_orphan(runner, calls, update_fails):
    """update 阶段炸掉：用例 FAIL，但插入的 42 号记录被兜底清扫删掉。"""
    report = runner.run()
    case = report["cases"][0]
    assert case["status"] == "FAIL"
    # 兜底清理走了预告+确认删除，与业务删除同一套安全流程
    assert {"entity": "book", "id": 42} in report["cleanup"]["removed"]
    assert report["cleanup"]["failed"] == []
    deletes = [c for c in calls if c["path"] == "/api/biz/book/delete"]
    assert deletes, "兜底清理必须发出删除请求"
    assert deletes[-1]["body"]["confirmToken"] == "ctok"


def test_passing_case_no_double_delete(runner, calls, update_fails):
    """用例自己删干净了：登记簿销账，兜底不再重复删。"""
    update_fails["on"] = False
    report = runner.run()
    assert report["cases"][0]["status"] == "PASS"
    assert report["cleanup"]["removed"] == []
    assert report["cleanup"]["failed"] == []
    deletes = [c for c in calls if c["path"] == "/api/biz/book/delete"]
    assert len(deletes) == 1, "只应有用例自己的那一次删除"
