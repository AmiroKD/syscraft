"""运行时验收执行器：读 acceptance.json，以黑盒方式打 HTTP 接口，出报告。

设计依据《运行时管理设计 v0.10》3.3：验收项由生成器给出，运行时只负责「怎么跑」。
本文件支持清单里的三种 check 类型（health / endpoints / crud / status），
够覆盖第一周第 4 步的五项验收；规则级（计算、约束、状态机）与性能项留到后续。
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path

import httpx


class CaseFailure(Exception):
    """一条断言失败：带上期望与实际，报告里直接可读。"""

    def __init__(self, message: str, expected=None, actual=None):
        super().__init__(message)
        self.expected = expected
        self.actual = actual


class AcceptanceRunner:
    def __init__(self, base_url: str, spec: dict, timeout: float = 10.0,
                 env: dict | None = None):
        self.spec = spec
        self.client = httpx.Client(base_url=base_url, timeout=timeout)
        # 本次运行的局部凭证（如流水线随机生成的验收口令）：显式传入、用完即弃，
        # 不写进 os.environ——进程级环境变量会被之后启动的所有子进程继承，是泄漏面
        self._env = env or {}
        self._tokens: dict[str, str] = {}
        self.run_id = uuid.uuid4().hex[:6]
        # 验收临时数据登记簿（审查 r5 留存项：中途失败无统一清理）：
        # (entity, base, record_id, headers) → 删除成功后销账；回合结束兜底清扫
        self._tracked: list[dict] = []

    def _env_get(self, name: str, default: str | None = None) -> str | None:
        """凭证解析：局部凭证优先，环境变量兜底（CLI 直跑时没有局部凭证）。"""
        if name in self._env:
            return self._env[name]
        return os.environ.get(name, default)

    # ------------------------------------------------------------------ 基础

    def close(self) -> None:
        self.client.close()

    def token(self, username: str) -> str:
        if username in self._tokens:
            return self._tokens[username]
        account = next((a for a in self.spec.get("accounts", []) if a["username"] == username), None)
        if account is None:
            raise CaseFailure(f"验收清单里没有账号 {username}")
        if "usernameRef" in account:
            username = self._env_get(account["usernameRef"].split(":", 1)[-1], "")
        env_name = account["passwordRef"].split(":", 1)[-1]
        password = self._env_get(env_name)
        if not password:
            raise CaseFailure(f"缺少验收凭证 {env_name}")
        response = self.client.post("/api/auth/login", json={"username": username, "password": password})
        body = response.json()
        if body.get("code") != 0:
            raise CaseFailure(f"验收账号 {username} 登录失败", expected="code=0", actual=body)
        token = body["data"]["token"]
        self._tokens[username] = token
        return token

    def headers(self, auth: str | None) -> dict:
        if not auth:
            return {}
        if auth == "none":
            return {}
        return {"Authorization": f"Bearer {self.token(auth)}"}

    # ------------------------------------------------------------------ 用例

    def run(self) -> dict:
        results = []
        try:
            for case in self.spec.get("cases", []):
                results.append(self._run_case(case))
        finally:
            cleanup = self._cleanup_leftovers()

        failed = [r for r in results if r["status"] == "FAIL"]
        summary = {
            "total": len(results),
            "passed": len([r for r in results if r["status"] == "PASS"]),
            "failed": len(failed),
            "blockFailed": len([r for r in failed if r["level"] == "block"]),
        }
        return {
            "modelVersion": self.spec.get("modelVersion"),
            "startedAt": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "runId": self.run_id,
            "result": "PASS" if summary["blockFailed"] == 0 else "FAIL",
            "summary": summary,
            "cases": results,
            "cleanup": cleanup,
        }

    def _run_case(self, case: dict) -> dict:
        base = {"id": case["id"], "title": case["title"], "level": case.get("level", "block")}
        try:
            # 驼峰类型名 → 蛇形方法名：confirmReuse -> _check_confirm_reuse
            check_type = "".join("_" + part.lower() if part.isupper() else part
                                 for part in case["check"]["type"]).lstrip("_")
            detail = getattr(self, "_check_" + check_type)(case["check"])
            return {**base, "status": "PASS", "detail": detail}
        except CaseFailure as failure:
            return {**base, "status": "FAIL", "message": str(failure),
                    "expected": failure.expected, "actual": failure.actual,
                    "suspects": case.get("suspects", [])}
        except (AttributeError, KeyError) as error:
            # 清单结构坏了（未知检查类型/缺字段）按单条 FAIL 处理：
            # 不能让一条坏清单炸掉整个验收回合（审查 r6）
            return {**base, "status": "FAIL",
                    "message": f"验收清单结构错误：{error}",
                    "suspects": case.get("suspects", [])}
        except httpx.HTTPError as error:
            return {**base, "status": "FAIL", "message": f"HTTP 异常：{error}",
                    "suspects": case.get("suspects", [])}

    # --------------------------------------------------------------- 各类断言

    def _check_health(self, check: dict) -> dict:
        response = self.client.get(check["path"])
        body = response.json()
        if response.status_code != 200 or body.get("status") != "UP":
            raise CaseFailure("健康检查不是 UP", expected="UP", actual=body)
        return {"status": body.get("status")}

    def _check_endpoints(self, check: dict) -> dict:
        base, ops, auth = check["base"], check["ops"], check.get("auth", "__acc_admin__")
        missing = []
        seen = {}
        for op in ops:
            response = self.client.post(f"{base}/{op}", json={}, headers=self.headers(auth))
            seen[op] = response.status_code
            # 参数校验失败（400）也算端点存在；404/405 才是没这个端点
            if response.status_code in (404, 405):
                missing.append(op)
        if missing:
            raise CaseFailure(f"缺少端点：{missing}", expected=ops, actual=seen)
        return seen

    def _check_status(self, check: dict) -> dict:
        auth = check.get("auth")
        payload = check.get("auth_body")
        response = self.client.post(check["path"], json=payload or {}, headers=self.headers(auth))
        if response.status_code != check["expect"]:
            raise CaseFailure(
                f"{check['path']} 期望 {check['expect']}，实际 {response.status_code}",
                expected=check["expect"], actual=response.json(),
            )
        return {"status": response.status_code, "body": response.json()}

    def _check_confirm_reuse(self, check: dict) -> dict:
        base, entity, auth = check["base"], check["entity"], check.get("account", "__acc_admin__")
        headers = self.headers(auth)

        sample = dict(check["sample"])
        for field in check.get("unique_fields", []):
            if isinstance(sample.get(field), str):
                sample[field] = f"{sample[field]}-{self.run_id}"
        record_id = self._post(f"{base}/insert", sample, headers)["data"]
        self._track(entity, base, record_id, headers)

        ticket = self._post("/api/confirm/preview", {"entity": entity, "ids": [record_id]}, headers)
        token = ticket["data"]["token"]

        first = self.client.post(f"{base}/delete", json={"ids": [record_id], "confirmToken": token},
                                 headers=headers)
        if first.status_code != 200:
            raise CaseFailure("首次核销失败", expected=200, actual=first.json())
        self._untrack(entity, record_id)

        second = self.client.post(f"{base}/delete", json={"ids": [record_id], "confirmToken": token},
                                  headers=headers)
        if second.status_code != 409:
            raise CaseFailure("同一令牌第二次核销未被拒绝", expected=409, actual=second.json())
        return {"firstStatus": first.status_code, "secondStatus": second.status_code}

    def _check_crud(self, check: dict) -> dict:
        base, auth = check["base"], check.get("account", "__acc_admin__")
        headers = self.headers(auth)

        sample = dict(check["sample"])
        # 唯一列加随机后缀：否则同一份验收跑第二次会撞唯一键
        for field in check.get("unique_fields", []):
            if isinstance(sample.get(field), str):
                sample[field] = f"{sample[field]}-{self.run_id}"

        inserted = self._post(f"{base}/insert", sample, headers)
        record_id = inserted.get("data")
        if record_id is None:
            raise CaseFailure("新增未返回主键", expected="data=id", actual=inserted)
        self._track(check["entity"], base, record_id, headers)

        rows = self._query_by_id(base, record_id, headers)
        if len(rows) != 1:
            raise CaseFailure("新增后按主键查不到", expected=1, actual=len(rows))

        # 改一个字符串字段再查回，验证「改」真的落库
        changed_field = next((k for k, v in sample.items() if isinstance(v, str)), None)
        if changed_field:
            updated = dict(sample)
            updated["id"] = record_id
            updated[changed_field] = f"{sample[changed_field]}-upd"
            self._post(f"{base}/update", updated, headers)
            rows = self._query_by_id(base, record_id, headers)
            if rows[0].get(changed_field) != updated[changed_field]:
                raise CaseFailure(f"更新未生效：{changed_field}", expected=updated[changed_field], actual=rows[0].get(changed_field))

        # 删除走确认流程：预告（拿令牌与影响行数）→ 携令牌执行，与 Agent 的真实调用一致
        ticket = self._post("/api/confirm/preview",
                            {"entity": check["entity"], "ids": [record_id]}, headers)
        if ticket["data"]["impactRows"] != 1:
            raise CaseFailure("预告的影响行数不是 1", expected=1, actual=ticket["data"])
        deleted = self._post(f"{base}/delete",
                             {"ids": [record_id], "confirmToken": ticket["data"]["token"]}, headers)
        if deleted.get("data") != 1:
            raise CaseFailure("删除影响行数不是 1", expected=1, actual=deleted)
        self._untrack(check["entity"], record_id)

        if self._query_by_id(base, record_id, headers):
            raise CaseFailure("删除后仍能查到", expected=0, actual=1)

        return {"id": record_id, "updatedField": changed_field}

    # ------------------------------------------------------------------ 工具

    def _track(self, entity: str, base: str, record_id, headers: dict) -> None:
        self._tracked.append({"entity": entity, "base": base,
                              "id": record_id, "headers": headers})

    def _untrack(self, entity: str, record_id) -> None:
        self._tracked = [t for t in self._tracked
                         if not (t["entity"] == entity and t["id"] == record_id)]

    def _cleanup_leftovers(self) -> dict:
        """回冬结束兜底清扫：所有登记在册、还没被用例自己删掉的记录，
        走预告+确认流程删掉。中途失败（断言不过/HTTP 异常/意外异常）
        留下的半截数据都在这里收尾；清理本身失败只记进报告，不再抛。"""
        removed, failed = [], []
        for item in list(self._tracked):
            try:
                ticket = self._post("/api/confirm/preview",
                                    {"entity": item["entity"], "ids": [item["id"]]},
                                    item["headers"])
                self._post(f"{item['base']}/delete",
                           {"ids": [item["id"]],
                            "confirmToken": ticket["data"]["token"]},
                           item["headers"])
                removed.append({"entity": item["entity"], "id": item["id"]})
            except Exception as error:  # noqa: BLE001 清理失败不能掩埋主报告
                failed.append({"entity": item["entity"], "id": item["id"],
                               "error": str(error)})
            finally:
                self._untrack(item["entity"], item["id"])
        return {"removed": removed, "failed": failed}

    def _post(self, path: str, payload: dict, headers: dict) -> dict:
        response = self.client.post(path, json=payload, headers=headers)
        body = response.json()
        if response.status_code >= 400 or body.get("code") != 0:
            raise CaseFailure(f"{path} 失败", expected="HTTP 200 / code 0", actual=body)
        return body

    def _query_by_id(self, base: str, record_id, headers: dict) -> list:
        payload = {"filter": {"op": "AND", "conditions": [
            {"field": "id", "operator": "EQ", "value": record_id}]}}
        body = self._post(f"{base}/query", payload, headers)
        return body["data"]["rows"]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="SysCraft 运行时验收")
    parser.add_argument("--base-url", required=True)
    parser.add_argument("--spec", required=True, help="acceptance.json 路径")
    parser.add_argument("--report", required=True, help="报告输出路径")
    args = parser.parse_args(argv)

    spec = json.loads(Path(args.spec).read_text(encoding="utf-8"))
    runner = AcceptanceRunner(args.base_url, spec)
    try:
        report = runner.run()
    finally:
        runner.close()

    report_path = Path(args.report)
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    for case in report["cases"]:
        mark = "PASS" if case["status"] == "PASS" else "FAIL"
        print(f"[{mark}] {case['id']:<24} {case['title']}")
        if case["status"] == "FAIL":
            print(f"       期望 {case.get('expected')} / 实际 {case.get('actual')}")

    summary = report["summary"]
    print(f"\n结果 {report['result']}：{summary['passed']}/{summary['total']} 通过，"
          f"{summary['blockFailed']} 项阻断失败")
    print(f"报告：{report_path}")
    return 0 if report["result"] == "PASS" else 1


if __name__ == "__main__":
    sys.exit(main())
