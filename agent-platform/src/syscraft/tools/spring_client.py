"""调用产物（Spring）的唯一通道。

平台侧不直连数据库、不拼业务 SQL，所有读写都走生成的接口（禁令 D6 / 权限 6 节）。
令牌由调用方透传：平台不解析、不续签，只把它放进 Authorization 头转发。
"""

from __future__ import annotations

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
    def __init__(self, base_url: str | None = None, timeout: float | None = None):
        self._base_url = base_url or config.SPRING_BASE_URL
        self._timeout = timeout or config.SPRING_TIMEOUT_SECONDS

    async def _post(self, path: str, payload: dict, token: str | None) -> dict[str, Any]:
        headers = {"Content-Type": "application/json"}
        if token:
            headers["Authorization"] = f"Bearer {token}"
        try:
            async with httpx.AsyncClient(base_url=self._base_url, timeout=self._timeout) as client:
                response = await client.post(path, json=payload, headers=headers)
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

    async def query(self, entity: str, request: dict, token: str) -> dict[str, Any]:
        body = await self._post(f"/api/biz/{entity}/query", request, token)
        return body["data"]

    async def aggregate(self, entity: str, request: dict, token: str) -> list[dict]:
        body = await self._post(f"/api/biz/{entity}/aggregate", request, token)
        return body["data"]
