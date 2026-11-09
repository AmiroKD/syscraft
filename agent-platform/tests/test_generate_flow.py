"""生成流：意图识别、澄清、计划确认、流水线编排（全 fake，不碰 LLM/网络/mvn）。"""

from __future__ import annotations

import asyncio
import json
import shutil
import tempfile
from pathlib import Path
from types import SimpleNamespace

import pytest

import syscraft.agent.generate as gen_module
import syscraft.runtime.pipeline as pipeline_module
from syscraft.agent.generate import GenerationFlow
from syscraft.conversation.store import SessionStore
from syscraft.requirement.extractor import ExtractResult, Question

MODEL = {
    "entities": [{"entity": "book", "display_name": "图书",
                  "fields": [{"column": "name", "property": "name", "semantic": "书名", "type": "VARCHAR"}],
                  "rules": {"computed": [], "constraints": [], "states": []}}],
    "grants": [], "assumptions": [],
}
PLAN_RESULT = ExtractResult(model=MODEL, plan="将生成：\n- 图书（book）", assumptions=["借阅关系先不建"])
QUESTIONS_RESULT = ExtractResult(questions=[Question(question="借阅要登记谁吗", default="登记经办人")])

# 每轮 _extract 都新建一个抽取器实例，脚本按实例给；calls 记录 (requirement, answers)
FAKE_EXTRACTOR_SCRIPT: list = []
FAKE_CALLS: list = []


class FakeExtractor:
    """手写 __init__ 时不要套 @dataclass——装饰器生成的 __init__ 会把它覆盖，
    生成流传进来的 existing_entities 就成了 TypeError（实测踩过）。"""

    def __init__(self, **kwargs):
        self.script = FAKE_EXTRACTOR_SCRIPT.pop(0)

    def extract(self, requirement, answers=None):
        FAKE_CALLS.append((requirement, answers))
        return self.script.pop(0)


class FakePipeline:
    captured: dict = {}

    def __init__(self, **kwargs):
        FakePipeline.captured = kwargs

    def run(self, model_path):
        self.captured["notify"]("④ 编译", True, "8 个文件")
        return SimpleNamespace(ok=True, failed_step=None,
                               steps=[SimpleNamespace(ok=True, detail="9/9 通过", name="x")])


async def no_stop(self, emit):
    return None


@pytest.fixture()
def env(monkeypatch):
    # mkdtemp 代替 tmp_path：沙箱对复用临时根目录的 mkdir 有 EEXIST 怪癖（见 test_store）
    root = Path(tempfile.mkdtemp(prefix="syscraft-gen-test-"))
    models = root / "models"
    models.mkdir()
    monkeypatch.setattr(gen_module.config, "MODELS_DIR", models)
    monkeypatch.setattr(gen_module.config, "PROJECT_ROOT", root)
    # 测试环境没有真实密钥：不 patch 的话 _extract 会走「未配置模型」分支，流程根本不进来
    monkeypatch.setattr(gen_module.config, "LLM_API_KEY", "test-key")
    monkeypatch.setattr(pipeline_module, "_port_busy", lambda port: False)
    monkeypatch.setattr("syscraft.requirement.extractor.RequirementExtractor", FakeExtractor)
    FAKE_EXTRACTOR_SCRIPT.clear()
    FAKE_CALLS.clear()
    yield root
    shutil.rmtree(root, ignore_errors=True)


@pytest.fixture()
def store(env):
    return SessionStore(root=env / "sessions")


# ---------------------------------------------------------------- 意图识别

@pytest.mark.anyio
async def test_not_generation_intent_returns_none(store):
    flow = GenerationFlow(client=None)
    session = store.create(user_id=1)
    assert await flow.handle("查一下订单", "tk", session, emit=lambda e: None) is None


@pytest.mark.anyio
async def test_generation_intent_detected():
    flow = GenerationFlow(client=None)
    assert flow.is_generation_intent("帮我做一个图书管理系统")
    assert flow.is_generation_intent("新建一个库存管理")
    assert not flow.is_generation_intent("帮我生成一份订单统计报表")  # 无系统/管理对象
    assert not flow.is_generation_intent("查订单")


# ---------------------------------------------------------------- 澄清与计划

@pytest.mark.anyio
async def test_clarification_then_answer_then_plan(store, env):
    FAKE_EXTRACTOR_SCRIPT.append([QUESTIONS_RESULT])
    FAKE_EXTRACTOR_SCRIPT.append([PLAN_RESULT])
    flow = GenerationFlow(client=None)
    session = store.create(user_id=1)

    first = await flow.handle("做一个图书管理系统", "tk", session, lambda e: None)
    assert "借阅要登记谁吗" in first["reply"]
    assert session.get_slot("gen_state") == "awaiting_answers"

    second = await flow.handle("按默认的来", "tk", session, lambda e: None)
    assert "确认生成" in second["reply"]
    assert session.get_slot("gen_state") == "awaiting_plan"
    # 回答轮的入参：需求还是原话，回答是用户消息
    requirement, answers = FAKE_CALLS[-1]
    assert requirement == "做一个图书管理系统" and answers == ["按默认的来"]
    # 模型文件落盘且版本号已写入
    model_path = Path(session.get_slot("gen_model_path"))
    assert model_path.exists()
    assert json.loads(model_path.read_text(encoding="utf-8"))["model_version"].startswith("v")


@pytest.mark.anyio
async def test_cancel_plan(store, env):
    FAKE_EXTRACTOR_SCRIPT.append([PLAN_RESULT])
    flow = GenerationFlow(client=None)
    session = store.create(user_id=1)
    await flow.handle("做一个图书管理系统", "tk", session, lambda e: None)
    result = await flow.handle("算了", "tk", session, lambda e: None)
    assert "取消" in result["reply"]
    assert session.get_slot("gen_state") is None


# ---------------------------------------------------------------- 流水线编排

@pytest.mark.anyio
async def test_pipeline_runs_and_emits_events(store, env, monkeypatch):
    FAKE_EXTRACTOR_SCRIPT.append([PLAN_RESULT])
    events: list[dict] = []
    monkeypatch.setattr(gen_module, "Pipeline", FakePipeline)
    monkeypatch.setattr(gen_module.GenerationFlow, "_stop_old_product", no_stop)

    flow = GenerationFlow(client=None)
    session = store.create(user_id=1)
    await flow.handle("做一个图书管理系统", "tk", session, events.append)
    confirmed = await flow.handle("确认生成", "tk", session, events.append)
    assert "开始生成" in confirmed["reply"]

    # 流水线在后台线程跑：让事件循环转几圈等它完成
    for _ in range(100):
        if session.get_slot("gen_state") is None:
            break
        await asyncio.sleep(0.05)
    assert session.get_slot("gen_state") is None  # 超时也没清就是漏了

    kinds = [e["type"] for e in events]
    assert "phase" in kinds and "text" in kinds
    # keep_running 已传给流水线：新版本要留在 8080 承接业务
    assert FakePipeline.captured.get("keep_running") is True
