"""修复循环：三段管道守卫、语法预检、投影、熔断（假 LLM，不碰真 mvn）。"""

from __future__ import annotations

from pathlib import Path

import json
import shutil
import tempfile

import pytest

from syscraft.codegen.repair.circuit import CircuitBreaker, signature
from syscraft.codegen.repair.events import EventLog
from syscraft.codegen.repair.llm_client import RepairLLM
from syscraft.codegen.repair.projector import project
from syscraft.codegen.repair.repair_loop import repair
from syscraft.codegen.repair.tools import ToolExec, syntax_precheck


@pytest.fixture()
def version_dir():
    """最小工程：generated 里一个会编译通过的 Java 文件（用于编辑类测试）。

    用 tempfile.mkdtemp 而不是 pytest 的 tmp_path：后者在复用临时根目录时
    会被本机沙箱的 mkdir 代理绊倒（EEXIST），与被测代码无关。
    """
    tmp_path = Path(tempfile.mkdtemp(prefix="syscraft-repair-test-"))
    src = tmp_path / "src/main/java/com/generated/generated/service"
    src.mkdir(parents=True)
    (src / "DemoService.java").write_text(
        "package x;\npublic class DemoService {\n    int add(int a, int b) {\n        return a + b;\n    }\n}\n",
        encoding="utf-8")
    platform = tmp_path / "src/main/java/com/generated/platform/common"
    platform.mkdir(parents=True)
    (platform / "Guard.java").write_text("package y;\npublic class Guard {}\n", encoding="utf-8")
    (tmp_path / "pom.xml").write_text("<project/>", encoding="utf-8")
    yield tmp_path
    shutil.rmtree(tmp_path, ignore_errors=True)


def fake_build_ok():
    return True, "BUILD SUCCESS"


def fake_build_fail():
    return False, "/x/DemoService.java:[3] 找不到符号"


# ---------------------------------------------------------------- 三段管道

def test_edit_requires_unique_old_string(version_dir):
    tools = ToolExec(version_dir, build_runner=fake_build_ok)
    result = tools.run({"tool": "edit_file", "path": "src/main/java/com/generated/generated/service/DemoService.java",
                        "old_string": "int a", "new_string": "int c"})
    # "int a" 在文件里只出现一次 → 通过；换一个出现两次的串验证拒绝
    (version_dir / "src/main/java/com/generated/generated/service/DemoService.java").write_text(
        "package x;\npublic class DemoService {\n    int x;\n    int x;\n}\n", encoding="utf-8")
    result = tools.run({"tool": "edit_file", "path": "src/main/java/com/generated/generated/service/DemoService.java",
                        "old_string": "int x;", "new_string": "int y;"})
    assert result["ok"] is False and "必须唯一" in result["error"]


def test_edit_rejects_protected_paths(version_dir):
    tools = ToolExec(version_dir, build_runner=fake_build_ok)
    for path in ["src/main/java/com/generated/platform/common/Guard.java", "pom.xml"]:
        result = tools.run({"tool": "edit_file", "path": path, "old_string": "class", "new_string": "classX"})
        assert result["ok"] is False, path
        assert "保护路径" in result["error"] or "只允许" in result["error"]


def test_edit_rejects_path_escape(version_dir):
    tools = ToolExec(version_dir, build_runner=fake_build_ok)
    result = tools.run({"tool": "edit_file", "path": "../../etc/passwd", "old_string": "a", "new_string": "b"})
    assert result["ok"] is False and "越出" in result["error"]


def test_syntax_precheck_rolls_back(version_dir):
    tools = ToolExec(version_dir, build_runner=fake_build_ok)
    path = "src/main/java/com/generated/generated/service/DemoService.java"
    result = tools.run({"tool": "edit_file", "path": path,
                        "old_string": "return a + b;", "new_string": "return a + b;\n    { 没闭合"})
    assert result["ok"] is False and "已回滚" in result["error"]
    text = (version_dir / path).read_text(encoding="utf-8")
    assert "return a + b;" in text and "没闭合" not in text


def test_read_file_caps_at_200_lines(version_dir):
    src = version_dir / "src/main/java/com/generated/generated/service/DemoService.java"
    src.write_text("\n".join(f"line{i}" for i in range(500)), encoding="utf-8")
    tools = ToolExec(version_dir, build_runner=fake_build_ok)
    result = tools.run({"tool": "read_file", "path": src.relative_to(version_dir).as_posix(),
                        "start_line": 1, "end_line": 500})
    assert len(result["output"].splitlines()) <= 200


# ---------------------------------------------------------------- 投影

def test_project_keeps_recent_full_and_compresses_remote():
    tmp_path = Path(tempfile.mkdtemp(prefix="syscraft-proj-test-"))
    log = EventLog(tmp_path / "events.jsonl")
    for i in range(8):
        log.append("ReadFile", path=f"a{i}.java", ok=True, brief=f"read a{i}")
    log.append("CompileFailed", errors="Demo.java:3 找不到符号")
    text = project(log, tmp_path, turn=1, step=3, max_turns=3, max_steps=15)
    assert "a0.java" not in text or "更早" in text  # 远端只留摘要
    assert "找不到符号" in text
    assert "已改清单" in text


# ---------------------------------------------------------------- 熔断

def test_fingerprint_two_identical_turn_ends():
    breaker = CircuitBreaker(version_dir := __import__("pathlib").Path("."))
    assert breaker.record_turn_end("A.java:1 找不到符号\nB.java:2 不兼容") is None
    reason = breaker.record_turn_end("A.java:9 找不到符号\nB.java:9 不兼容")
    assert reason and "模板缺陷" in reason


def test_signature_ignores_line_numbers():
    assert signature("A.java:1 找不到符号") == signature("A.java:99 找不到符号")


def test_no_progress_breaker(version_dir):
    breaker = CircuitBreaker(version_dir)
    for _ in range(3):
        breaker.record_step_state(version_dir)
    assert breaker.check() and "空转" in breaker.check()


def test_token_budget_breaker(version_dir):
    breaker = CircuitBreaker(version_dir, token_budget=100)
    breaker.tokens(200)
    assert breaker.check() and "token" in breaker.check()


# ---------------------------------------------------------------- 循环编排

def test_loop_repairs_with_fake_llm(version_dir, monkeypatch):
    """假 LLM 走完「edit → build 成功」两步；llm_call 注入，解析走真 RepairLLM。"""
    script = iter([
        (json.dumps({"tool": "edit_file",
                     "path": "src/main/java/com/generated/generated/service/DemoService.java",
                     "old_string": "return a + b;", "new_string": "return a * 2;"}), 500),
        (json.dumps({"tool": "run_build"}), 100),
    ])
    llm = RepairLLM(llm_call=lambda messages: next(script))
    result = repair(version_dir, "Demo.java:3 找不到符号", llm=llm,
                    build_runner=fake_build_ok, log_path=version_dir / "events.jsonl")
    assert result.outcome == "SUCCESS"
    text = (version_dir / "src/main/java/com/generated/generated/service/DemoService.java").read_text(encoding="utf-8")
    assert "return a * 2;" in text


def test_loop_aborts_after_turns(version_dir):
    """每步都在改文件（状态有进展，无进展熔断不触发）、每轮错误文件都不同（指纹不触发）：
    单独验证 turn 熔断本身。"""
    calls = {"n": 0}
    marker = ["package x;"]
    java_path = "src/main/java/com/generated/generated/service/DemoService.java"

    def edit_comment(messages):
        calls["n"] += 1
        old, new = marker[0], marker[0] + f"\n// edit {calls['n']}"
        marker[0] = new
        return json.dumps({"tool": "edit_file", "path": java_path,
                           "old_string": old, "new_string": new}), 10

    builds = {"n": 0}
    def failing_build():
        builds["n"] += 1
        return False, f"Err{builds['n']}.java:[1] 错误各不相同"

    result = repair(version_dir, "Demo.java:3 找不到符号",
                    llm=RepairLLM(llm_call=edit_comment),
                    build_runner=failing_build, log_path=version_dir / "events.jsonl")
    assert result.outcome == "ABORT" and "轮用尽" in result.reason
    assert calls["n"] == 45  # 3 轮 × 15 步


def test_loop_aborts_on_identical_turn_errors(version_dir):
    """每轮结束时都 build 失败且错误相同：第二 turn 末触发错误指纹熔断。"""
    def always_build(messages):
        return json.dumps({"tool": "run_build"}), 10

    result = repair(version_dir, "Demo.java:3 找不到符号",
                    llm=RepairLLM(llm_call=always_build),
                    build_runner=fake_build_fail, log_path=version_dir / "events.jsonl")
    assert result.outcome == "ABORT" and "模板缺陷" in result.reason
