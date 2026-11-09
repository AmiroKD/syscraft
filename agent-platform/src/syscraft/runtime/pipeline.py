"""运行时流水线：《运行时管理设计》3 节的 ②生成 → ③建表 → ④编译（含修复）→ ⑤启动 → ⑥验收。

一条命令从模型 JSON 到验收报告。每个环节的产物都落盘，失败停在当步并把原因写进报告。

凭据与边界（这条必须说清楚）：
- biz 库 DDL 用 MCP 账号直连（pymysql）——建表本来就是平台的本职，MCP 账号就是给它的；
- adm 库的默认授权脚本用 mysql CLI 外部执行——**这是桥接**：设计禁止平台直连 adm，
  正式方案是给 Spring 补一个引导端点，等做设置面板前端时一并补上（记在待办）；
- 验收账号走 Spring 管理接口创建/停用（超管令牌来自环境变量），不碰 adm；
- 结构快照不需要任何脚本：产物启动时自查导出。
"""

from __future__ import annotations

import json
import os
import secrets
import shutil
import subprocess
import time
from dataclasses import dataclass, field
from pathlib import Path

import pymysql

from syscraft import config
from syscraft.codegen.generator import generate
from syscraft.codegen.repair.repair_loop import RepairCancelled, repair
from syscraft.runtime.acceptance import AcceptanceRunner

HEALTH_TIMEOUT_SECONDS = 90


class GenerationCancelled(RuntimeError):
    """用户在步骤边界主动取消——不是故障，报告与对话回复都按取消语义呈现。"""


@dataclass
class StepResult:
    name: str
    ok: bool
    detail: str = ""


@dataclass
class PipelineResult:
    version: str
    ok: bool = False
    failed_step: str | None = None
    steps: list[StepResult] = field(default_factory=list)
    # 步骤回调（对话式生成用来推 SSE）。挂在 result 上而不是 Pipeline 上——
    # record() 是 result 的方法，挂在 Pipeline 上会 AttributeError（实测踩过）
    notify: object | None = field(default=None, repr=False)
    # 顶层异常文本。failed_step 只指向最后一个「成功记录」的步骤，
    # 真实失败原因在这里——对话回复必须用它，否则用户看到的是空错误
    error: str = ""

    def record(self, name: str, ok: bool, detail: str = "") -> None:
        self.steps.append(StepResult(name, ok, detail))
        mark = "PASS" if ok else "FAIL"
        print(f"[{mark}] {name}" + (f"  {detail[:160]}" if detail else ""))
        if self.notify:
            self.notify(name, ok, detail)


class Pipeline:
    def __init__(self, project_root: Path | None = None, port: int = 8080,
                 *, notify=None, keep_running: bool = False,
                 should_cancel=None, on_event=None):
        """notify(name, ok, detail)：每步结束回调（对话式生成用它推 SSE 阶段事件）。
        keep_running：验收成功后不关产物——对话式生成的终态是新版本直接上线。
        should_cancel()：步骤边界轮询的取消标志（对话式生成传 threading.Event.is_set）。
        on_event(dict)：file/log 级原始事件回调（协议 3 节六种事件的后两种）。"""
        self.root = (project_root or config.PROJECT_ROOT).resolve()
        self.port = port
        self.process: subprocess.Popen | None = None
        self._notify = notify
        self._keep_running = keep_running
        self._should_cancel = should_cancel
        self._on_event = on_event

    def _emit(self, event: dict) -> None:
        if self._on_event:
            self._on_event(event)

    # ------------------------------------------------------------------ ②生成

    def step_stop_stale(self, result: PipelineResult) -> None:
        """停掉残留的旧产物（CLI 模式与对话式生成对齐，一条命令到底）。
        只匹配自家 jar；端口本就空闲则零开销。"""
        if not (_port_busy(self.port) or _healthy(self.port)):
            return
        subprocess.run(["pkill", "-f", "syscraft-skeleton-0.1.0.jar"], capture_output=True)
        deadline = time.time() + 15
        while (_port_busy(self.port) or _healthy(self.port)) and time.time() < deadline:
            time.sleep(0.5)
        if _port_busy(self.port):
            result.record("① 停旧产物", False, "端口仍被占用")
            raise RuntimeError("旧产物停止失败")
        result.record("① 停旧产物", True)

    def step_generate(self, model_path: Path, result: PipelineResult) -> Path:
        info = generate(model_path, self.root,
                        on_file=lambda relative: self._emit(
                            {"type": "file", "path": relative, "action": "write"}))
        result.record("② 生成", True, f"版本 {info['version']}，{sum(len(v) for v in info['files'].values())} 个文件")
        return Path(info["version_dir"])

    # ------------------------------------------------------------------ ③建表

    def step_apply_ddl(self, version: str, model: dict, result: PipelineResult) -> list[str]:
        """返回本次实际建表清单（取消/失败时报告里可见，孤儿表清理有据可查）。"""
        ddl_dir = self.root / "workspace/scripts/ddl"
        scripts = sorted(ddl_dir.glob(f"{version}_*.sql"))
        created = [entity["entity"] for entity in model.get("entities", [])]
        connection = pymysql.connect(
            host="127.0.0.1", port=3306,
            user=os.environ.get("MCP_USER", ""), password=os.environ.get("MCP_PWD", ""),
            database="biz", charset="utf8mb4",
        )
        applied = 0
        try:
            with connection.cursor() as cursor:
                for script in scripts:
                    for statement in _statements(script):
                        cursor.execute(statement)
                        applied += 1
            connection.commit()

            # 表结构漂移检测：CREATE TABLE IF NOT EXISTS 对已存在的表不生效，
            # 而 LLM 每次抽取的字段名有方差（实测：price → unit_price）。
            # 不查就会「编译通过、运行 500」，必须在这里把漂移暴露出来。
            with connection.cursor() as cursor:
                for entity in model.get("entities", []):
                    cursor.execute(
                        "SELECT COLUMN_NAME FROM information_schema.COLUMNS "
                        "WHERE TABLE_SCHEMA = %s AND TABLE_NAME = %s",
                        ("biz", entity["entity"]))
                    existing = {row[0] for row in cursor.fetchall()}
                    expected = {f["column"] for f in entity["fields"]}
                    missing = expected - existing
                    if missing:
                        raise RuntimeError(
                            f"表 {entity['entity']} 已存在且与模型不一致（缺列：{sorted(missing)}）。"
                            "可能是旧版本残留：请换实体名或手工 DROP 后重跑。")
        finally:
            connection.close()
        result.record("③ 建表（biz）", True, f"{len(scripts)} 个脚本，{applied} 条语句")
        return created

    # --------------------------------------------------- 模型快照（明示扩展）

    def step_upload_model(self, model_path: Path, model: dict, result: PipelineResult) -> None:
        """把模型原件上传到 adm_model_snapshot（POST /api/meta/model，仅超管）。
        审计用途：什么需求生成了什么系统。版本号取尾数存 BIGINT；
        非数字版本（回归模型 r1 之类）跳过并记录，不阻塞流水线。"""
        import httpx
        version = model["model_version"]
        digits = "".join(ch for ch in version if ch.isdigit())
        if not digits:
            result.record("③a 模型快照", True, f"版本 {version} 非数字，跳过快照")
            return
        token, _ = _spring_session()
        response = httpx.post(f"{config.SPRING_BASE_URL}/api/meta/model",
                              headers={"Authorization": f"Bearer {token}"},
                              json={"version": version,
                                    "modelJson": json.dumps(model, ensure_ascii=False)},
                              timeout=10)
        if response.status_code != 200:
            result.record("③a 模型快照", False, response.text[:200])
            raise RuntimeError("模型快照上传失败")
        result.record("③a 模型快照", True, f"版本 {version}")

    # ------------------------------------------------------ 默认授权（引导端点）

    def step_apply_grants(self, version: str, model: dict, result: PipelineResult) -> None:
        """默认授权走 Spring 引导端点（POST /api/admin/grants/bootstrap）。
        必须排在启动之后：新实体的结构快照要等新产物启动时才导出，
        引导端点的「目标必须在快照里」校验才放得过去。
        （原先的 mysql CLI 桥接随本方法改写移除。）"""
        import httpx
        token, _ = _spring_session()
        payload = [{"role": g["role"], "entity": g["entity"], "access": g["access"]}
                   for g in model.get("grants", [])]
        response = httpx.post(f"{config.SPRING_BASE_URL}/api/admin/grants/bootstrap",
                              headers={"Authorization": f"Bearer {token}"},
                              json=payload, timeout=15)
        if response.status_code != 200:
            detail = response.text[:200]
            result.record("③b 默认授权（bootstrap）", False, detail)
            raise RuntimeError(f"默认授权引导失败：{detail}")
        data = response.json().get("data", {})
        result.record("③b 默认授权（bootstrap）", True,
                      f"应用 {data.get('applied', 0)} 条，幂等跳过 {data.get('skipped', 0)} 条")

    # ------------------------------------------------------------------ ④编译

    def step_build(self, version_dir: Path, model_version: str, result: PipelineResult) -> None:
        def log_event(source: str, text: str) -> None:
            self._emit({"type": "log", "source": source, "text": text[-400:]})

        ok, output = _maven_build(version_dir)
        if ok:
            result.record("④ 编译", True)
            return

        # 编译失败交修复循环：3 轮 × 15 步，成功即完成本步
        error_file = version_dir / "target/compile-errors.txt"
        error_file.write_text(output, encoding="utf-8")
        log_event("maven", output)
        # 局部名不能用 repair——会遮蔽导入的修复函数（UnboundLocalError，实测踩过）
        repair_result = repair(version_dir, output,
                               log_path=self.root / "workspace/repair" / f"{model_version}-pipeline.jsonl",
                               on_log=lambda text: log_event("repair", text),
                               should_cancel=self._should_cancel)
        if repair_result.outcome == "SUCCESS":
            result.record("④ 编译（经修复循环）", True, f"{repair_result.steps_used} 步修复")
            return
        result.record("④ 编译（经修复循环）", False, repair_result.reason or "修复失败")
        raise RuntimeError(f"编译失败且修复未成功：{repair_result.reason}")

    # ------------------------------------------------------------------ ⑤启动

    def step_start(self, version_dir: Path, result: PipelineResult) -> None:
        log_file = self.root / "workspace/logs" / f"{version_dir.name}-app.log"
        log_file.parent.mkdir(parents=True, exist_ok=True)

        # 端口被占（通常是上次中断留下的产物进程）必须先失败：
        # 健康检查会打到旧进程身上，产生「启动成功、验收 404」的假阳性（实测踩过）
        if _healthy(self.port) or _port_busy(self.port):
            result.record("⑤ 启动", False, f"端口 {self.port} 已被占用，请先停掉残留的产物进程")
            raise RuntimeError(f"端口 {self.port} 被占用")

        jar = version_dir / "target/syscraft-skeleton-0.1.0.jar"
        self.process = subprocess.Popen(
            ["java", "-jar", str(jar), f"--server.port={self.port}"],
            stdout=open(log_file, "w"), stderr=subprocess.STDOUT,
            env={**os.environ},
            # 守护化：产物进独立会话，不随流水线/平台进程组退出而陪葬
            # （keep_running 的语义靠它才成立，实测踩过）；pkill -f 仍按名可及
            start_new_session=True,
        )
        # 给端口绑定失败留出暴露时间：起 3 秒后子进程若已退出，直接判失败，
        # 免得健康检查打到别的进程身上
        time.sleep(3)
        if self.process.poll() is not None:
            result.record("⑤ 启动", False, "进程提前退出（多为端口冲突），见 " + str(log_file))
            raise RuntimeError("产物进程退出")

        deadline = time.time() + HEALTH_TIMEOUT_SECONDS
        while time.time() < deadline:
            if self.process.poll() is not None:
                result.record("⑤ 启动", False, "进程提前退出，见 " + str(log_file))
                raise RuntimeError("产物进程退出")
            if _healthy(self.port):
                result.record("⑤ 启动", True, f"port {self.port} pid {self.process.pid}")
                return
            time.sleep(1)
        result.record("⑤ 启动", False, "健康检查超时")
        raise RuntimeError("产物健康检查超时")

    # ------------------------------------------------------------------ ⑥验收

    def step_acceptance(self, version: str, result: PipelineResult) -> dict:
        spec_path = self.root / "templates/acceptance" / f"{version}.json"
        admin_token, admin_id = _spring_session()

        # 验收账号走管理接口创建，随机口令只存在内存里；验收完停用（等效删除的权限效果，
        # 账号行留下反而是审计痕迹；契约没有删除用户端点，见运行时 3.3）
        os.environ["ACC_USER_PWD"] = _ensure_account(admin_token, "__acc_user__", "USER")
        spec = json.loads(spec_path.read_text(encoding="utf-8"))
        runner = AcceptanceRunner(f"http://127.0.0.1:{self.port}", spec)
        try:
            report = runner.run()
        finally:
            runner.close()
            for username in ("__acc_admin__", "__acc_user__"):
                user_id = _find_user(admin_token, username)
                if user_id:
                    _disable_user(admin_token, user_id)

        report_path = self.root / "workspace/logs" / f"acceptance-{version}.json"
        report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        ok = report["result"] == "PASS"
        result.record("⑥ 自动验收", ok,
                      f"{report['summary']['passed']}/{report['summary']['total']} 通过")
        if not ok:
            raise RuntimeError("自动验收存在阻断失败，见 " + str(report_path))
        return report

    def step_mark_current(self, version: str, result: PipelineResult) -> None:
        """验收通过后把 adm_state.current_version 指到新版本。
        失败不阻塞上线（状态可重登），但记录留痕。"""
        import httpx
        try:
            token, _ = _spring_session()
            response = httpx.put(f"{config.SPRING_BASE_URL}/api/system/state",
                                 headers={"Authorization": f"Bearer {token}"},
                                 json={"currentVersion": version}, timeout=5)
            ok = response.status_code == 200
            detail = "" if ok else response.text[:160]
        except Exception as error:  # noqa: BLE001 状态登记失败不能毁掉一次成功的生成
            ok, detail = False, str(error)[:160]
        result.record("⑦ 版本登记", ok, detail or f"current_version = {version}")

    # ------------------------------------------------------------------ 编排

    def run(self, model_path: Path) -> PipelineResult:
        model = json.loads(model_path.read_text(encoding="utf-8"))
        version = model["model_version"]
        result = PipelineResult(version=version, notify=self._notify)
        print(f"=== SysCraft 流水线：{model_path.name} → v{version[1:] if version.startswith('v') else version} ===")

        # 记住生成前的运行版本：取消/失败后把旧产物拉回来（P0-1）。
        # 必须在停旧产物之前读——旧产物一停，state 端点就读不到了。
        # 惰性导入防环：switcher.switch 内部也 import pipeline 的端口探测
        from syscraft.runtime import switcher as version_switcher
        previous_version = version_switcher.current_version()
        created_tables: list[str] = []  # 已建表清单（失败/取消进报告，孤儿表清理有据）

        try:
            self.step_stop_stale(result)
            # 以下每个步骤前都检查取消：步骤是边界——一旦进到 mvn/健康等待内部
            # 就让它跑完当步，避免留下半编译的目录或半启的进程（取消只发生在边界）
            self._check_cancel()
            version_dir = self.step_generate(model_path, result)
            self._check_cancel()
            created_tables = self.step_apply_ddl(version, model, result)
            self._check_cancel()
            self.step_build(version_dir, version, result)
            self._check_cancel()
            self.step_start(version_dir, result)
            self._check_cancel()
            # 快照与授权都需要在线的 Spring：新产物启动后，快照导出、引导校验才放行
            self.step_upload_model(model_path, model, result)
            self.step_apply_grants(version, model, result)
            self._check_cancel()
            report = self.step_acceptance(version, result)
            self.step_mark_current(version, result)
            result.ok = True
            summary = {"version": version, "ok": True, "steps": [s.__dict__ for s in result.steps],
                       "acceptance": report["summary"]}
        except (GenerationCancelled, RepairCancelled):
            # RepairCancelled：修复循环（最长的一步）内部的取消，语义与边界取消一致
            result.ok = False
            result.failed_step = result.steps[-1].name if result.steps else "?"
            result.error = "用户取消生成"
            result.record("✕ 取消", False, "用户取消，已停止后续步骤")
            summary = {"version": version, "ok": False, "failedStep": result.failed_step,
                       "steps": [s.__dict__ for s in result.steps], "error": "用户取消生成",
                       "cancelled": True, "tablesCreated": created_tables}
        except Exception as error:
            result.ok = False
            result.failed_step = result.steps[-1].name if result.steps else "?"
            result.error = str(error)
            summary = {"version": version, "ok": False, "failedStep": result.failed_step,
                       "steps": [s.__dict__ for s in result.steps], "error": str(error),
                       "tablesCreated": created_tables}
        finally:
            # 对话式生成成功后不关产物：新版本直接上线承接业务对话（运行时 5 节）。
            # 取消/失败都走这里停产物——已启动的进程不陪取消标志一起活着
            if not (self._keep_running and result.ok):
                self.stop()
                # 自动回滚：旧产物被本流水线杀了，失败/取消后必须有人把它扶起来，
                # 否则一次取消 = 全平台停机（实测踩过：v8 取消后 v7 躺尸）
                if previous_version and previous_version != version:
                    self._rollback(previous_version, result)

        report_path = self.root / "workspace/logs" / f"pipeline-{version}.json"
        report_path.parent.mkdir(parents=True, exist_ok=True)
        report_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"\n结果：{'成功' if result.ok else '失败'}，报告：{report_path}")
        return result

    def _rollback(self, version: str, result: PipelineResult) -> None:
        """失败/取消出口：把生成前的版本重新拉起。回滚失败不吞报告，明示人工介入。"""
        from syscraft.runtime import switcher as version_switcher
        try:
            version_switcher.switch(version)
            result.record("⑧ 回滚旧版本", True, f"已恢复运行 {version}")
        except version_switcher.SwitchError as error:
            result.record("⑧ 回滚旧版本", False,
                          f"{error}；需人工介入：POST /api/runtime/switch 到 {version}")

    def _check_cancel(self) -> None:
        if self._should_cancel and self._should_cancel():
            raise GenerationCancelled("用户取消生成")

    def stop(self) -> None:
        if self.process and self.process.poll() is None:
            self.process.terminate()
            try:
                self.process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                self.process.kill()


# ---------------------------------------------------------------- 工具函数

def _statements(script: Path) -> list[str]:
    """剥注释行后按分号切分——DDL 里没有存储过程，这个粒度够用且不会切错。"""
    body = "\n".join(line for line in script.read_text(encoding="utf-8").splitlines()
                     if not line.strip().startswith("--"))
    return [s.strip() for s in body.split(";") if s.strip()]


def _maven_build(version_dir: Path) -> tuple[bool, str]:
    maven = shutil.which("mvn") or Path.home() / ".m2/wrapper/dists/apache-maven-3.9.12/57e1dc81/bin/mvn"
    process = subprocess.run([str(maven), "-B", "-q", "package", "-DskipTests"],
                             cwd=version_dir, capture_output=True, text=True, timeout=600)
    return process.returncode == 0, (process.stdout + process.stderr)[-4000:]


def _port_busy(port: int) -> bool:
    """健康接口没起来不代表端口空着——占用者可能是个不响应 /actuator 的进程。"""
    import socket
    with socket.socket() as sock:
        sock.settimeout(1)
        return sock.connect_ex(("127.0.0.1", port)) == 0


def _healthy(port: int) -> bool:
    try:
        import httpx
        response = httpx.get(f"http://127.0.0.1:{port}/actuator/health", timeout=2)
        return response.status_code == 200
    except Exception:
        return False


def _random_password() -> str:
    alphabet = "ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz23456789@#$%"
    return "".join(secrets.choice(alphabet) for _ in range(14))


def _spring_session() -> tuple[str, int]:
    """以超管身份登录产物（口令来自环境变量），供验收账号管理使用。"""
    import httpx
    base = config.SPRING_BASE_URL
    response = httpx.post(f"{base}/api/auth/login", json={
        "username": os.environ.get("ADMIN_USER", ""), "password": os.environ.get("ADMIN_INIT_PWD", ""),
    }, timeout=5)
    body = response.json()
    if body.get("code") != 0:
        raise RuntimeError(f"超管登录失败：{body.get('msg')}")
    data = body["data"]
    return data["token"], data["user"]["id"]


def _ensure_account(admin_token: str, username: str, role_code: str) -> str:
    """创建验收账号，返回明文口令。

    上次验收中断会留下同名账号（409）：走「重置口令 + 重新启用」复用，
    不要求人工清库——流水线要能从任何一步失败后重跑。
    """
    import httpx
    password = _random_password()
    response = httpx.post(f"{config.SPRING_BASE_URL}/api/admin/users",
                          headers={"Authorization": f"Bearer {admin_token}"},
                          json={"username": username, "password": password, "roleCode": role_code},
                          timeout=5)
    if response.status_code == 200:
        return password

    user_id = _find_user(admin_token, username)
    if response.status_code == 409 and user_id:
        reset = httpx.post(f"{config.SPRING_BASE_URL}/api/admin/users/{user_id}/password",
                           headers={"Authorization": f"Bearer {admin_token}"}, timeout=5)
        initial = reset.json().get("data", {}).get("initialPassword")
        if not initial:
            raise RuntimeError(f"验收账号 {username} 口令重置失败：{reset.text[:200]}")
        httpx.patch(f"{config.SPRING_BASE_URL}/api/admin/users/{user_id}",
                    headers={"Authorization": f"Bearer {admin_token}"},
                    json={"enabled": True}, timeout=5)
        return initial
    raise RuntimeError(f"验收账号 {username} 创建失败：{response.text[:200]}")


def _find_user(admin_token: str, username: str) -> int | None:
    import httpx
    response = httpx.get(f"{config.SPRING_BASE_URL}/api/admin/users",
                         params={"keyword": username}, headers={"Authorization": f"Bearer {admin_token}"},
                         timeout=5)
    for row in response.json().get("data", {}).get("rows", []):
        if row["username"] == username:
            return row["id"]
    return None


def _disable_user(admin_token: str, user_id: int) -> None:
    import httpx
    httpx.patch(f"{config.SPRING_BASE_URL}/api/admin/users/{user_id}",
                headers={"Authorization": f"Bearer {admin_token}"},
                json={"enabled": False}, timeout=5)


def main(argv: list[str] | None = None) -> int:
    import argparse
    parser = argparse.ArgumentParser(description="SysCraft 运行时流水线")
    parser.add_argument("--model", required=True, help="模型 JSON 路径")
    args = parser.parse_args(argv)
    result = Pipeline().run(Path(args.model))
    return 0 if result.ok else 1


if __name__ == "__main__":
    import sys
    sys.exit(main())
