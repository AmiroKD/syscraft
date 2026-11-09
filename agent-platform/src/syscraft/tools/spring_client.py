"""调用产物（Spring）的唯一通道。

平台侧不直连数据库、不拼业务 SQL，所有读写都走生成的接口（禁令 D6 / 权限 6 节）。
令牌由调用方透传：平台不解析、不续签，只把它放进 Authorization 头转发。
"""

from __future__ import annotations

import uuid
from typing import Any

import httpx

from syscraft import config


class SpringError(Exception):
    """产物侧返回了业务错误或网络错误。码与消息原样带给上层，不在这里翻译。"""

    def __init__(self, status: int, code: int | None, message: str):
        super().__init__(message)
        self.status = status
        self.code = code
        self.message = message


class SpringClient:
    def __init__(self, base_url: str | None = None, timeout: float | None = None,
                 transport: httpx.AsyncBaseTransport | None = None):
        self._base_url = base_url or config.SPRING_BASE_URL
        self._timeout = timeout or config.SPRING_TIMEOUT_SECONDS
        self._transport = transport  # 测试注入（httpx.MockTransport）

    async def _post(self, path: str, payload: dict, token: str | None,
                    request_id: str | None = None) -> dict[str, Any]:
        return await self._request("POST", path, payload, token, request_id=request_id)

    async def _request(self, method: str, path: str, payload: dict | None,
                       token: str | None, request_id: str | None = None) -> dict[str, Any]:
        headers = {"Content-Type": "application/json"}
        if token:
            headers["Authorization"] = f"Bearer {token}"
        if request_id:
            headers["X-Request-Id"] = request_id
        # 写操作（带幂等键）超时后重试一次：超时 ≠ 失败，请求可能已到达产物，
        # 不重试的话上层（模型/用户）重发就是双插——这正是 DedupFilter 要接的场景。
        # 幂等键在重试间保持不变，产物侧命中回放，不会写第二次（审查 r5 P2）
        attempts = 2 if request_id else 1
        for attempt in range(attempts):
            try:
                async with httpx.AsyncClient(base_url=self._base_url, timeout=self._timeout,
                                             transport=self._transport) as client:
                    response = await client.request(method, path, json=payload, headers=headers)
                break
            except httpx.TimeoutException as error:
                if request_id is None or attempt + 1 >= attempts:
                    # 读操作（无幂等键）或已重试过：超时按失败上抛。
                    # 注意保持 503：_identity 只兜底 502/503 的「产物不在线」
                    detail = (f"产物服务响应超时（已携幂等键重试一次）：{error}"
                              if request_id else f"产物服务响应超时：{error}")
                    raise SpringError(503, None, detail) from error
            except httpx.HTTPError as error:
                # 产物没起来时给一句人话，而不是让 httpx 的堆栈冒到前端
                raise SpringError(503, None, f"产物服务不可达：{error}") from error

        try:
            body = response.json()
        except ValueError as error:
            raise SpringError(response.status_code, None, "产物返回的不是 JSON") from error

        if response.status_code >= 400 or body.get("code") != 0:
            raise SpringError(response.status_code, body.get("code"), body.get("msg") or "调用失败")
        return body

    async def login(self, username: str, password: str) -> dict[str, Any]:
        body = await self._post("/api/auth/login", {"username": username, "password": password}, None)
        return body["data"]

    async def me(self, token: str) -> dict[str, Any]:
        """当前用户（含 id）。会话绑定、身份展示都从这里取——平台不自己解析令牌。"""
        body = await self._request("GET", "/api/auth/me", None, token)
        return body["data"]

    async def query(self, entity: str, request: dict, token: str) -> dict[str, Any]:
        body = await self._post(f"/api/biz/{entity}/query", request, token)
        return body["data"]

    async def aggregate(self, entity: str, request: dict, token: str) -> list[dict]:
        body = await self._post(f"/api/biz/{entity}/aggregate", request, token)
        return body["data"]

    async def preview_delete(self, entity: str, ids: list[int], token: str) -> dict[str, Any]:
        """删除预告：拿确认令牌与影响行数，不执行任何删除。"""
        body = await self._post("/api/confirm/preview", {"entity": entity, "ids": ids}, token)
        return body["data"]

    async def insert(self, entity: str, row: dict, token: str) -> int:
        body = await self._post(f"/api/biz/{entity}/insert", row, token,
                                request_id=uuid.uuid4().hex)
        return body["data"]

    async def update(self, entity: str, record: dict, token: str) -> int:
        body = await self._post(f"/api/biz/{entity}/update", record, token,
                                request_id=uuid.uuid4().hex)
        return body["data"]

    async def delete(self, entity: str, ids: list[int], confirm_token: str, token: str) -> int:
        body = await self._post(
            f"/api/biz/{entity}/delete",
            {"ids": ids, "confirmToken": confirm_token},
            token, request_id=uuid.uuid4().hex,
        )
        return body["data"]
