"""回滚兜底与 ⑧ 步骤持久化（D3：验收失败回滚未恢复产物、报告无回滚记录）。"""
from __future__ import annotations

import pytest

from syscraft.runtime import switcher as version_switcher
from syscraft.runtime.pipeline import Pipeline, PipelineResult
from syscraft.runtime.switcher import SwitchError


def _pipeline() -> Pipeline:
    return Pipeline(keep_running=True)


def test_rollback_fallback_starts_jar_when_switch_fails(monkeypatch):
    """switch 抛 SwitchError 时，兜底直接拉起旧版本并登记进程。"""
    adopted = []

    def raise_switch(version: str) -> dict:
        raise SwitchError("旧产物停止失败，端口仍被占用")

    monkeypatch.setattr(version_switcher, "switch", raise_switch)
    monkeypatch.setattr(version_switcher, "_start_and_wait", lambda v: 4242)
    monkeypatch.setattr(version_switcher, "adopt_started_process", lambda: adopted.append(1))

    result = PipelineResult(version="v15")
    _pipeline()._rollback("v14", result)
    step = result.steps[-1]
    assert step.name == "⑧ 回滚旧版本"
    assert step.ok is True
    assert "v14" in step.detail and "4242" in step.detail


def test_rollback_records_manual_intervention_when_all_fails(monkeypatch):
    """switch 与直拉都失败：⑧ 记 False 并给出可执行的人工指令。"""
    def raise_switch(version: str) -> dict:
        raise SwitchError("起不来")

    def raise_start(version: str) -> int:
        raise SwitchError("健康等待超时")

    monkeypatch.setattr(version_switcher, "switch", raise_switch)
    monkeypatch.setattr(version_switcher, "_start_and_wait", raise_start)

    result = PipelineResult(version="v15")
    _pipeline()._rollback("v14", result)
    step = result.steps[-1]
    assert step.ok is False
    assert "java -jar" in step.detail  # 人工指令可执行（产物死了连登录都不可用）


def test_rollback_success_path_unchanged(monkeypatch):
    def ok_switch(version: str) -> dict:
        return {"version": version, "pid": 1}

    monkeypatch.setattr(version_switcher, "switch", ok_switch)
    result = PipelineResult(version="v15")
    _pipeline()._rollback("v14", result)
    assert result.steps[-1].ok is True
