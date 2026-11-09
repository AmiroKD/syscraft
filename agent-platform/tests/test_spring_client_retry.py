"""SpringClient 幂等接线（审查 r5）：写操作带 X-Request-Id、超时重试一次同键。

背景缺陷：DedupFilter（产物侧幂等回放）已建成，但平台侧从未发送
X-Request-Id，也没有任何重试——写超时后上层重发就是双插，幂等链路实际未接线。
"""

from __future__ import annotations

import asyncio

import httpx
import pytest

from syscraft.tools.spring_client import SpringClient, SpringError


def make_client(handler) -> SpringClient:
    return SpringClient(base_url="http://test", transport=httpx.MockTransport(handler))


def test_insert_carries_request_id():
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["rid"] = request.headers.get("X-Request-Id")
        return httpx.Response(200, json={"code": 0, "msg": "ok", "data": 42})

    assert asyncio.run(make_client(handler).insert("book", {"name": "x"}, "tok")) == 42
    assert seen["rid"] and len(seen["rid"]) == 32  # uuid4 hex


def test_write_timeout_retries_once_with_same_id():
    calls: list[str | None] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request.headers.get("X-Request-Id"))
        if len(calls) == 1:
            raise httpx.ConnectTimeout("boom", request=request)
        return httpx.Response(200, json={"code": 0, "msg": "ok", "data": 7})

    assert asyncio.run(make_client(handler).insert("book", {}, "tok")) == 7
    assert len(calls) == 2
    assert calls[0] == calls[1]  # 幂等键跨重试不变：产物侧命中回放，不会双插


def test_double_timeout_raises_503():
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectTimeout("boom", request=request)

    with pytest.raises(SpringError) as error:
        asyncio.run(make_client(handler).insert("book", {}, "tok"))
    # 保持 503：_identity 只兜底 502/503 的「产物不在线」，超时不能另立门户
    assert error.value.status == 503
    assert "已携幂等键重试一次" in error.value.message


def test_read_has_no_request_id_and_no_retry():
    calls: list[str | None] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request.headers.get("X-Request-Id"))
        raise httpx.ConnectTimeout("boom", request=request)

    with pytest.raises(SpringError) as error:
        asyncio.run(make_client(handler).query("book", {}, "tok"))
    assert error.value.status == 503
    assert len(calls) == 1 and calls[0] is None  # 读操作不带幂等键、不重试
