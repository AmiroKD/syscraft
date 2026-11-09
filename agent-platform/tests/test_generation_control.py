"""生成控制：取消边界、file/log 事件回调、取消权限（全 fake，不碰 LLM/mvn/网络）。"""

from __future__ import annotations

import json
import shutil
import time
import tempfile
from pathlib import Path

import pytest

import syscraft.runtime.pipeline as pipeline_module
from syscraft.agent.generate import GenerationFlow
from syscraft.codegen.generator import generate
from syscraft.codegen.repair.llm_client import RepairLLM
from syscraft.codegen.repair.repair_loop import repair
from syscraft.runtime.pipeline import Pipeline
from syscraft.tools.spring_client import SpringClient

MODEL = {
    "entities": [{"entity": "book", "display_name": "图书", "class_name": "Book",
                  "owner_field": None,
                  "fields": [
                      {"column": "id", "property": "id", "semantic": "主键", "type": "BIGINT",
                       "primary_key": True, "nullable": False, "unique": True,
                       "visibility": "PLAIN", "sensitive": False},
                      {"column": "name", "property": "name", "semantic": "书名", "type": "VARCHAR",
                       "primary_key": False, "nullable": True, "unique": False,
                       "visibility": "PLAIN", "sensitive": False},
                  ],
                  "rules": {"computed": [], "constraints": [], "states": []}}],
    "grants": [],
}


@pytest.fixture()
def sandbox(request, monkeypatch):
    """mkdtemp 沙箱：模型文件写进去，workspace 产物用真项目根（骨架与模板在那）。

    每个测试用独立版本号（tx-<测试名散列>）：共用一个版本号会在同一次
    pytest 内互相踩残留——OneDrive 目录里 rmtree(ignore_errors) 吞错，
    删不净就给下一个测试埋雷（实测踩过）。
    """
    version = "tx" + str(abs(hash(request.node.name)) % 100000)
    tmp = Path(tempfile.mkdtemp(prefix="syscraft-cancel-test-"))
    model_path = tmp / "model.json"
    model_path.write_text(json.dumps({**MODEL, "model_version": version},
                                     ensure_ascii=False), encoding="utf-8")
    # 端口探测全部假接：不碰真机端口
    monkeypatch.setattr(pipeline_module, "_port_busy", lambda port: False)
    monkeypatch.setattr(pipeline_module, "_healthy", lambda port: False)
    yield model_path, version
    shutil.rmtree(tmp, ignore_errors=True)
    root = pipeline_module.config.PROJECT_ROOT

    def purge(path: Path) -> None:
        """OneDrive 目录里文件可能被同步进程短暂占用，rmtree 会静默吞错——
        残留目录会踩坏后续测试（版本目录已存在），必须重试到删净。"""
        if path.is_dir():
            for _ in range(3):
                shutil.rmtree(path, ignore_errors=True)
                if not path.exists():
                    return
                time.sleep(0.3)
            raise RuntimeError(f"清理失败：{path}")

    for rel in (f"workspace/versions/{version}", f"templates/acceptance/{version}.json"):
        p = root / rel
        if p.is_dir():
            purge(p)
        else:
            p.unlink(missing_ok=True)
    (root / f"workspace/logs/pipeline-{version}.json").unlink(missing_ok=True)
    for d in ("workspace/scripts/ddl", "workspace/scripts/grants"):
        for p in (root / d).glob(f"{version}_*"):
            p.unlink(missing_ok=True)


# ---------------------------------------------------------------- 取消边界

def test_cancel_at_first_boundary(sandbox):
    """should_cancel 恒真：第一步边界即取消，任何步骤都不执行，报告标 cancelled。"""
    model_path, version = sandbox
    pipeline = Pipeline(should_cancel=lambda: True)
    result = pipeline.run(model_path)
    assert result.ok is False
    assert result.error == "用户取消生成"
    assert [s.name for s in result.steps] == ["✕ 取消"]
    # 产物目录没有创建
    assert not (pipeline_module.config.PROJECT_ROOT / "workspace/versions" / version).exists()


def test_cancel_report_marks_cancelled(sandbox):
    """取消语义要进流水线报告：排查时能区分「失败」与「主动取消」。"""
    model_path, version = sandbox
    pipeline = Pipeline(project_root=pipeline_module.config.PROJECT_ROOT,
                        should_cancel=lambda: True)
    pipeline.run(model_path)
    body = json.loads((pipeline_module.config.PROJECT_ROOT
                       / "workspace/logs" / f"pipeline-{version}.json").read_text(encoding="utf-8"))
    assert body["cancelled"] is True and body["error"] == "用户取消生成"


def test_cancel_flag_checked_between_steps(sandbox):
    """首次边界放行到生成、第二次边界拦住建表：取消粒度是「步骤边界」。"""
    model_path, _ = sandbox
    state = {"n": 0}

    def cancel_after_first():
        state["n"] += 1
        return state["n"] > 1  # 第二次 _check_cancel（生成完成后）即取消

    pipeline = Pipeline(should_cancel=cancel_after_first)
    result = pipeline.run(model_path)
    assert result.ok is False and result.error == "用户取消生成"
    # 生成步骤已完成（产物在），但建表被拦
    assert any("生成" in s.name for s in result.steps)
    assert not any("建表" in s.name for s in result.steps)


# ---------------------------------------------------------------- file 事件

def test_generate_reports_every_written_file(sandbox):
    """on_file 回调覆盖全部写出文件，且每个路径都真实存在。"""
    model_path, _ = sandbox
    seen: list[str] = []
    info = generate(model_path, pipeline_module.config.PROJECT_ROOT, on_file=seen.append)
    version_dir = Path(info["version_dir"])
    assert len(seen) >= 7  # 六文件 + GeneratedMetadata + DDL + grants + acceptance
    for relative in seen:
        # 产物内路径相对版本目录，脚本路径相对项目根——两者之一必须存在
        assert (version_dir / relative).exists() or (
            pipeline_module.config.PROJECT_ROOT / relative).exists(), relative
    java_files = [p for p in seen if p.endswith(".java")]
    assert java_files, "六文件与元数据都该出现在 file 事件里"


def test_generate_without_callback_still_works(sandbox):
    """不传 on_file（CLI 路径）：行为与回调版完全一致。"""
    model_path, version = sandbox
    info = generate(model_path, pipeline_module.config.PROJECT_ROOT)
    assert info["version"] == version and info["files"]


# ---------------------------------------------------------------- log 事件

def test_repair_emits_step_logs():
    """修复循环每步发一条可读日志；build 成功路径两步可见。"""
    tmp = Path(tempfile.mkdtemp(prefix="syscraft-log-test-"))
    try:
        src = tmp / "src/main/java/com/generated/generated/service"
        src.mkdir(parents=True)
        (src / "DemoService.java").write_text(
            "package x;\npublic class DemoService {\n    int add(int a, int b) {\n        return a + b;\n    }\n}\n",
            encoding="utf-8")
        script = iter([
            (json.dumps({"tool": "edit_file", "path": "src/main/java/com/generated/generated/service/DemoService.java",
                         "old_string": "return a + b;", "new_string": "return a * 2;"}), 500),
            (json.dumps({"tool": "run_build"}), 100),
        ])
        logs: list[str] = []
        result = repair(tmp, "Demo.java:3 找不到符号",
                        llm=RepairLLM(llm_call=lambda messages: next(script)),
                        build_runner=lambda: (True, "BUILD SUCCESS"),
                        log_path=tmp / "events.jsonl",
                        on_log=logs.append)
        assert result.outcome == "SUCCESS"
        assert len(logs) == 2
        assert "[轮1步1] edit_file ✓" in logs[0]
        assert "[轮1步2] run_build ✓" in logs[1]
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


# ---------------------------------------------------------------- 取消权限

def test_request_cancel_permissions():
    flow = GenerationFlow(SpringClient())
    # 没有生成在跑：拒绝
    assert "没有进行中的生成" in flow.request_cancel(1, True)
    # 模拟 owner=7 的生成在跑
    flow._running_owner = 7
    assert "仅发起人或超管" in flow.request_cancel(1, False)
    assert not flow._cancel.is_set()  # 被拒的请求不碰标志
    assert flow.request_cancel(7, False) is None and flow._cancel.is_set()
    flow._cancel.clear()
    assert flow.request_cancel(1, True) is None and flow._cancel.is_set()
