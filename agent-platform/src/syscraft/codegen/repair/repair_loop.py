"""修复循环主体（《代码生成器设计》6.3）。

repair(编译错误) → 3 轮 × 15 步 → 每步：投影 → 模型给一个动作 → 三段管道 → 记事件 →
build 成功即 SUCCESS → 六道熔断随时 ABORT。事件全量落盘，这是留痕本身（6.9）。
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from syscraft.codegen.repair.circuit import CircuitBreaker
from syscraft.codegen.repair.events import EventLog
from syscraft.codegen.repair.llm_client import RepairLLM
from syscraft.codegen.repair.projector import project
from syscraft.codegen.repair.tools import ToolExec

MAX_TURNS = 3
MAX_STEPS = 15


@dataclass
class RepairResult:
    outcome: str            # SUCCESS / ABORT
    reason: str | None
    turns_used: int
    steps_used: int
    log_path: Path


def repair(version_dir: Path, compile_errors: str, llm: RepairLLM | None = None,
           build_runner=None, log_path: Path | None = None,
           max_turns: int = MAX_TURNS, max_steps: int = MAX_STEPS) -> RepairResult:
    version_dir = version_dir.resolve()
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    log_path = log_path or Path("workspace/repair") / f"{version_dir.name}-{stamp}.jsonl"
    log = EventLog(log_path)
    tools = ToolExec(version_dir, build_runner=build_runner)
    llm = llm or RepairLLM()
    breaker = CircuitBreaker(version_dir)

    log.append("CompileFailed", errors=compile_errors)
    steps_used = 0
    for turn in range(1, max_turns + 1):
        for step in range(1, max_steps + 1):
            steps_used += 1
            context = project(log, version_dir, turn, step, max_turns, max_steps)
            try:
                action, tokens = llm.decide(context)
                breaker.tokens(tokens)
            except Exception as error:
                # 模型两次都给不出合法动作：记失败事件，这一步作废继续走完 turn
                log.append("LLMError", error=str(error)[:300], ok=False)
                continue

            result = tools.run(action)
            event_type = {"edit_file": "EditFile", "read_file": "ReadFile",
                          "grep": "Grep", "list_files": "ListFiles",
                          "run_build": "Build"}.get(action.get("tool", ""), "Unknown")
            log.append(event_type, tool=action.get("tool"), ok=result.get("ok", False),
                       brief=result.get("brief"), error=result.get("error"),
                       diff=result.get("diff"), output=(result.get("output") or "")[:1200]
                       if event_type == "Build" else None)

            if action.get("tool") == "run_build":
                if result.get("ok"):
                    log.append("Repaired", turns=turn, steps=step, ok=True)
                    return RepairResult("SUCCESS", None, turn, steps_used, log_path)
                breaker.record_turn_end(result.get("output") or "")

            if action.get("tool") == "edit_file":
                # 无进展只统计编辑尝试：读文件是正当的信息收集，交给步数上限管——
                # 把探索性 read 也算「空转」，模型在动手前会被掐死（实测踩过）
                breaker.record_step_state(version_dir)
            reason = breaker.check()
            if reason:
                log.append("CircuitBreak", reason=reason, ok=False)
                return RepairResult("ABORT", reason, turn, steps_used, log_path)

        reason = breaker.record_turn_end(_last_build_errors(log))
        if reason:
            log.append("CircuitBreak", reason=reason, ok=False)
            return RepairResult("ABORT", reason, turn, steps_used, log_path)

    return RepairResult("ABORT", f"{max_turns} 轮用尽仍未编译通过", max_turns, steps_used, log_path)


def _last_build_errors(log: EventLog) -> str:
    for event in reversed(log.all()):
        if event.type == "Build" and not event.payload.get("ok"):
            return str(event.payload.get("output") or "")
    return ""


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="SysCraft 修复循环")
    parser.add_argument("--dir", required=True, help="版本目录")
    parser.add_argument("--errors", required=True, help="编译错误文本文件")
    args = parser.parse_args(argv)

    errors = Path(args.errors).read_text(encoding="utf-8", errors="replace")
    result = repair(Path(args.dir), errors)
    print(json.dumps({
        "outcome": result.outcome, "reason": result.reason,
        "turns": result.turns_used, "steps": result.steps_used,
        "log": str(result.log_path),
    }, ensure_ascii=False, indent=2))
    return 0 if result.outcome == "SUCCESS" else 1


if __name__ == "__main__":
    sys.exit(main())
