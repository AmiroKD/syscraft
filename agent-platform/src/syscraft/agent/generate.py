"""生成模式（GENERATE）：对话里从一句话需求到可运行系统。

流程是确定性编排，LLM 只负责需求抽取（《需求到模式映射》5 节：校验类的活儿
确定性干）——这让每一步都可观测：

    生成意图 → 抽取（可能追问一次）→ 生成计划（人确认）→ 流水线（事件流）→ 新版本上线

与《智能体工具协议》的关系：协议的生成组工具（preview_plan/apply_plan）面向
「模型自主触发渲染」的形态；这里实现为编排层直调——模型不掌握渲染的触发权，
确认权在用户手上。这是有意的收紧，与确认令牌同一哲学。

事件流归 app 层的 StreamHub 管（本模块只往回调里推）；全局生成锁在
stream_hub.generation_lock，app 层用它拒绝所有会话的并发消息（设计 5 节）。
"""

from __future__ import annotations

import asyncio
import json
import re
import subprocess
import threading
import time
from pathlib import Path
from typing import Callable

from syscraft import config
from syscraft.runtime.pipeline import Pipeline
from syscraft.tools.spring_client import SpringClient

# 生成意图：动词 + 可能量词 + 对象（系统/管理/平台/模块）。
# 注意 (一?个) 的写法：`一个?` 是「一+可选的个」，会把「做一个…系统」漏掉（实测踩过）。
# 误报的代价很低——下一轮是计划确认，用户回「取消」即可，计划文本本身就会暴露误解
GEN_INTENT = re.compile(r"(生成|搭建|做|新建|开发|建)(一?个)?.*(系统|管理|平台|模块)")

CONFIRM_PLAN = {"确认生成", "开始生成", "确认", "好的", "开始吧", "开始"}
CANCEL_PLAN = {"取消", "算了", "不生成", "不了", "取消生成", "先不"}


class GenerationFlow:
    # 生成触发允许的角色（P0-2：全局生成锁会停掉在线产物并锁所有会话，
    # 不能让任意 USER 一句话把平台打瘫——DoS 通道必须关死）
    GENERATION_ROLES = {"SUPER_ADMIN", "ADMIN"}

    def __init__(self, client: SpringClient):
        self._client = client
        # 全局生成锁意味着同一时刻至多一个生成在跑，一个取消标志就够。
        # threading.Event 而非 asyncio.Event：pipeline.run 在 to_thread 线程里轮询它
        self._cancel = threading.Event()
        self._running_owner: int | None = None  # 当前生成的发起者（user id）

    def is_generation_intent(self, message: str) -> bool:
        return bool(GEN_INTENT.search(message or ""))

    async def handle(self, message: str, token: str, session,
                     emit: Callable[[dict], None],
                     me: dict | None = None) -> dict | None:
        """处理生成模式的一条消息；返回 None 表示不是生成请求（回退业务模式）。

        me：当前用户（app 层从 _identity 传入）。生成触发按角色门槛校验，
        fail-closed：me 缺失一律拒绝——app 层忘传不能变成裸奔。
        进入本方法的都会落盘用户原话——业务图不再重复落。
        """
        state = session.get_slot("gen_state") or "idle"

        if state == "running":
            session.append("user", "text", message)
            # 生成锁才是运行的唯一真相：平台重启后锁随进程消失，
            # 但 gen_state 槽位持久化在 SQLite——不复位的话用户永远卡在「生成中」
            from syscraft.conversation.stream_hub import generation_lock
            if not generation_lock.locked():
                session.set_slot("gen_state", None)
                self._running_owner = None
                self._cancel.clear()
            else:
                return {"reply": "系统正在生成中（见下方进度），可点进度面板上的「取消生成」终止。"}

        if state == "awaiting_plan":
            session.append("user", "text", message)
            text = (message or "").strip()
            if text in CANCEL_PLAN:
                session.set_slot("gen_state", None)
                reply = "已取消本次生成，需求没有落地。想重新开始直接描述即可。"
                session.append("assistant", "text", reply)
                return {"reply": reply}
            if text in CONFIRM_PLAN:
                return await self._run_pipeline(session, emit)
            # 计划没确认前改需求 = 重新抽取
            return await self._extract(message, session, answers=None)

        if state == "awaiting_answers":
            session.append("user", "text", message)
            # 用户对追问的回答（设计 4.3：回答过就不再问，按默认值建模并回述）
            session.set_slot("gen_state", None)
            return await self._extract(session.get_slot("gen_requirement") or message,
                                       session, answers=[message])

        # idle：先判生成意图，不是就交回业务模式（不落盘，业务图自己落）
        if not self.is_generation_intent(message):
            return None
        # 生成触发角色门槛（fail-closed）：命中意图但无权 → 落盘后拒绝
        if (me or {}).get("roleCode") not in self.GENERATION_ROLES:
            session.append("user", "text", message)
            reply = ("生成新系统需要管理员权限（当前角色无权触发）。\n"
                     "你可以继续查询/登记现有系统的数据；需要新系统请联系管理员。")
            session.append("assistant", "text", reply)
            return {"reply": reply}

        session.append("user", "text", message)
        session.set_slot("gen_requirement", message)
        return await self._extract(message, session, answers=None)

    # ---------------------------------------------------------------- 抽取

    async def _extract(self, requirement: str, session, answers: list[str] | None) -> dict:
        if not config.LLM_API_KEY:
            return {"reply": "生成模式需要配置模型（LLM_API_KEY），当前未配置。"}

        from syscraft.requirement.extractor import RequirementExtractor
        from syscraft.requirement.model_catalog import catalog
        extractor = RequirementExtractor(existing_entities={e.entity for e in catalog().all()})

        try:
            result = await asyncio.to_thread(extractor.extract, requirement, answers)
        except Exception as error:  # 模型故障 / 自修正后校验仍不过
            session.set_slot("gen_state", None)
            reply = f"需求解析失败：{error}"
            session.append("assistant", "error", {"message": reply})
            return {"reply": reply}

        if result.needs_clarification:
            session.set_slot("gen_state", "awaiting_answers")
            lines = "\n".join(f"{i}. {q.question}（默认：{q.default}）"
                              for i, q in enumerate(result.questions, 1))
            reply = f"开始建模前有几个问题（直接回复答案即可，或回复「按默认」跳过）：\n{lines}"
            session.append("assistant", "text", reply)
            return {"reply": reply}

        return self._present_plan(result, session)

    def _present_plan(self, result, session) -> dict:
        model = result.model
        version = _next_version()
        model["model_version"] = version
        model_path = config.MODELS_DIR / f"auto_gen.{version}.json"
        model_path.write_text(json.dumps(model, ensure_ascii=False, indent=2), encoding="utf-8")

        session.set_slot("gen_model_path", str(model_path))
        session.set_slot("gen_state", "awaiting_plan")
        entities = "、".join(e["display_name"] for e in model["entities"])
        reply = (f"需求已理解，将生成{len(model['entities'])}个实体：{entities}（版本 {version}）。\n\n"
                 f"{result.plan}\n\n确认无误请回复「确认生成」，要调整直接重新描述。")
        session.append("assistant", "plan", {"summary": reply, "version": version,
                                             "assumptions": result.assumptions})
        return {"reply": reply}

    # ---------------------------------------------------------------- 取消

    def request_cancel(self, user_id: int, is_super_admin: bool) -> str | None:
        """请求取消当前生成。返回 None 表示已受理，否则是拒绝原因。

        取消权只给发起人或超管——生成是全局锁，但它占据的是发起者的需求，
        无关者不该能射落别人的任务。"""
        if self._running_owner is None:
            return "当前没有进行中的生成"
        if user_id != self._running_owner and not is_super_admin:
            return "仅发起人或超管可取消生成"
        self._cancel.set()
        return None

    # ---------------------------------------------------------------- 流水线

    async def _run_pipeline(self, session, emit: Callable[[dict], None]) -> dict:
        model_path = Path(session.get_slot("gen_model_path") or "")
        if not model_path.exists():
            session.set_slot("gen_state", None)
            return {"reply": "找不到生成计划对应的模型文件，请重新描述需求。"}

        from syscraft.conversation.stream_hub import generation_lock
        if generation_lock.locked():
            return {"reply": "另一个会话正在生成系统，请稍后再试。"}
        await generation_lock.acquire()
        session.set_slot("gen_state", "running")
        version = json.loads(model_path.read_text(encoding="utf-8"))["model_version"]
        reply = f"开始生成（版本 {version}），全程约 2 分钟，进度见下方事件。"
        session.append("assistant", "text", reply)

        try:
            me = session.meta().get("user_id")
            self._running_owner = int(me) if me is not None else None
        except (TypeError, ValueError):
            self._running_owner = None
        self._cancel.clear()

        async def task():
            try:
                await self._stop_old_product(emit)
                emit({"type": "phase", "step": "流水线开始", "ok": True, "detail": f"版本 {version}"})
                pipeline = Pipeline(
                    notify=lambda name, ok, detail: emit(
                        {"type": "phase", "step": name, "ok": ok, "detail": detail}),
                    keep_running=True,
                    should_cancel=self._cancel.is_set,
                    on_event=emit)
                result = await asyncio.to_thread(pipeline.run, model_path)
                if result.ok:
                    from syscraft.requirement.model_catalog import catalog
                    catalog.cache_clear()  # 新实体要让业务模式的工具白名单看见
                    passed = result.steps[-1].detail or "通过"
                    reply = f"生成完成：版本 {version} 已上线（自动验收 {passed}）。现在可以直接问我新系统里的数据了。"
                elif result.error == "用户取消生成":
                    reply = f"已取消生成（版本 {version}）。需求没有落地，产出的半成品留在 workspace 供检查。"
                else:
                    reply = (f"生成失败于「{result.failed_step}」：{result.error[:260]}。"
                             "产物保留在 workspace/versions 供排查，需求没有上线。")
                session.append("assistant", "text", reply)
                emit({"type": "text", "text": reply})
            except Exception as error:  # noqa: BLE001 线程里任何异常都要落到会话里
                reply = f"生成异常中断：{error}"
                session.append("assistant", "error", {"message": reply})
                emit({"type": "error", "message": reply})
            finally:
                generation_lock.release()
                self._running_owner = None
                self._cancel.clear()
                session.set_slot("gen_state", None)

        asyncio.create_task(task())
        return {"reply": reply}

    async def _stop_old_product(self, emit: Callable[[dict], None]) -> None:
        """运行组 stop_app 的语义：生成前把旧产物停掉，端口让给新版本。
        只匹配自家 jar 的进程名，不碰其他 java 进程。"""
        from syscraft.runtime.pipeline import _port_busy
        if not _port_busy(config.SPRING_PORT):
            return
        emit({"type": "phase", "step": "停止旧产物", "ok": True, "detail": "让出端口"})
        subprocess.run(["pkill", "-f", "syscraft-skeleton-0.1.0.jar"], capture_output=True)
        deadline = time.time() + 15
        while _port_busy(config.SPRING_PORT) and time.time() < deadline:
            await asyncio.sleep(0.5)


def _next_version() -> str:
    """扫 models 与 workspace/versions 里已占用的版本号，取下一个空闲的。"""
    used: set[str] = set()
    for path in config.MODELS_DIR.glob("*.json"):
        try:
            used.add(json.loads(path.read_text(encoding="utf-8")).get("model_version", ""))
        except (ValueError, OSError):
            continue
    versions_dir = config.PROJECT_ROOT / "workspace" / "versions"
    if versions_dir.exists():
        used |= {p.name for p in versions_dir.iterdir() if p.is_dir()}
    n = 1
    while f"v{n}" in used:
        n += 1
    return f"v{n}"
