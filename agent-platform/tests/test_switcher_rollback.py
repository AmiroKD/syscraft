"""版本切换失败回滚（审查 r6 回归锚点）。

背景缺陷：switch() 停旧产物成功、起新版本失败时直接抛错——旧产物已死、
新产物没起来，全平台停机需人工。与生成失败自动回滚（P0-1）是同族漏洞，
但切版本这条出口漏了。

修复后的不变量：
- 起新失败 → 尽力拉回切换前的版本；
- 回滚也失败 → 错误消息明示人工介入路径；
- 健康超时必须先清掉自己起的进程（否则占端口，回滚/重试都撞端口）。
"""

from __future__ import annotations

import pytest

import syscraft.runtime.pipeline as pipeline_module
import syscraft.runtime.switcher as switcher_module
from syscraft.runtime.switcher import SwitchError


@pytest.fixture()
def isolated(monkeypatch):
    """假接所有外部接触面：状态探测、端口探测、状态登记。
    （_port_busy/_healthy 是 switch 内部从 pipeline 惰性导入的，要打在源头）"""
    monkeypatch.setattr(pipeline_module, "_port_busy", lambda port: False)
    monkeypatch.setattr(pipeline_module, "_healthy", lambda port: False)
    monkeypatch.setattr(switcher_module, "_admin_token", lambda: "fake-token")
    monkeypatch.setattr(switcher_module.httpx, "put", lambda *a, **k: None)
    return monkeypatch


def test_switch_failure_rolls_back_to_previous(isolated):
    """起新失败、旧版本拉回成功：报错说明已回滚，且启动顺序正确。"""
    calls = []
    isolated.setattr(switcher_module, "_current_version", lambda: "v7")

    def fake_start(version):
        calls.append(version)
        if version == "v2":
            raise SwitchError("版本 v2 启动即退出")
        return 1234

    isolated.setattr(switcher_module, "_start_and_wait", fake_start)
    with pytest.raises(SwitchError) as error:
        switcher_module.switch("v2")
    assert calls == ["v2", "v7"]  # 先试新版，失败后拉回旧版
    assert "已自动拉回旧版本 v7" in str(error.value)


def test_switch_failure_when_rollback_also_fails(isolated):
    """起新失败、回滚也失败：明示人工介入，不假装成功。"""
    isolated.setattr(switcher_module, "_current_version", lambda: "v7")

    def fake_start(version):
        raise SwitchError(f"版本 {version} 起不来")

    isolated.setattr(switcher_module, "_start_and_wait", fake_start)
    with pytest.raises(SwitchError) as error:
        switcher_module.switch("v2")
    assert "需人工介入" in str(error.value)
    assert "v7" in str(error.value)


def test_switch_failure_without_previous_no_rollback(isolated):
    """切换前本无运行版本（previous=None）：无从回滚，原错误直接上抛。"""
    isolated.setattr(switcher_module, "_current_version", lambda: None)
    attempts = []

    def fake_start(version):
        attempts.append(version)
        raise SwitchError("版本 v2 启动即退出")

    isolated.setattr(switcher_module, "_start_and_wait", fake_start)
    with pytest.raises(SwitchError) as error:
        switcher_module.switch("v2")
    assert attempts == ["v2"]  # 没有第二次启动尝试
    assert "已自动拉回" not in str(error.value)


def test_start_and_wait_stops_process_on_health_timeout(isolated, monkeypatch):
    """健康检查超时：必须先 terminate 自己起的进程，不留占端口的残进程。"""
    terminated = []

    class FakeProcess:
        def poll(self):
            return None

        def terminate(self):
            terminated.append(True)

        def wait(self, timeout=None):
            return 0

        def kill(self):
            pass

    monkeypatch.setattr(switcher_module.subprocess, "Popen", lambda *a, **k: FakeProcess())
    monkeypatch.setattr(switcher_module, "HEALTH_TIMEOUT_SECONDS", 0)  # 立即判超时
    with pytest.raises(SwitchError) as error:
        switcher_module._start_and_wait("v9")
    assert terminated == [True]
    assert "健康检查超时" in str(error.value)


def test_switch_happy_path_registers_pid(isolated):
    """正常切换：返回新 pid（行为不回归锚点）。用真实存在的版本目录过 jar 检查。"""
    isolated.setattr(switcher_module, "_current_version", lambda: "v2")
    isolated.setattr(switcher_module, "_start_and_wait", lambda version: 4321)
    result = switcher_module.switch("v7")
    assert result == {"version": "v7", "pid": 4321}


def test_concurrent_switch_serialized(isolated, monkeypatch):
    """并发切换必须串行（审查 r5 留存项）：两个 switch 同时跑会在同一端口
    上互相 pkill/抢启动。用计数器验证不交错。"""
    import threading
    import time as _time

    isolated.setattr(switcher_module, "_current_version", lambda: "v7")
    inside = {"n": 0, "max": 0}
    guard = threading.Lock()

    def slow_start(version):
        with guard:
            inside["n"] += 1
            inside["max"] = max(inside["max"], inside["n"])
        _time.sleep(0.05)
        with guard:
            inside["n"] -= 1
        return 1234

    monkeypatch.setattr(switcher_module, "_start_and_wait", slow_start)
    threads = [threading.Thread(target=switcher_module.switch, args=("v2",))
               for _ in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert inside["max"] == 1, f"切换出现并发交错：同时在途 {inside['max']} 个"
