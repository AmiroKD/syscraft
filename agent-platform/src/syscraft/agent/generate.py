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
import uuid
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
            # 生成锁才是运行的唯一真相：平台重启后锁随进程消失，
            # 但 gen_state 槽位持久化在 SQLite——不复位的话用户永远卡在「生成中」
            from syscraft.conversation.stream_hub import generation_lock
            if not generation_lock.locked():
                session.set_slot("gen_state", None)
                self._running_owner = None
                self._cancel.clear()
                # 注意不在这里落盘用户消息：复位后走 idle/业务路径，各自的
                # 落盘点只此一次（审查 r5：先落盘再 fall-through 会双写历史）
            else:
                session.append("user", "text", message)
                return {"reply": "系统正在生成中（见下方进度），可点进度面板上的「取消生成」终止。"}

        if state == "awaiting_plan":
            session.append("user", "text", message)
            text = (message or "").strip()
            if text in CANCEL_PLAN:
                # 取消即清场：计划文件不删会在 models/ 里留一堆没人认领的 plan.*.json
                stale = session.get_slot("gen_model_path")
                if stale:
                    Path(stale).unlink(missing_ok=True)
                session.set_slot("gen_model_path", None)
                session.set_slot("gen_state", None)
                session.set_slot("gen_mode", None)
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

        # idle：先判「加字段」再判「建系统」——「给活动管理系统加个备注字段」
        # 会同时命中两个正则，增量语义应当优先（改一个字段不该整版重生成）
        from syscraft.requirement.amend import parse_amend
        from syscraft.requirement.model_catalog import catalog as _catalog
        amend = parse_amend(message, _catalog(online_only=True))
        if amend is not None:
            if (me or {}).get("roleCode") not in self.GENERATION_ROLES:
                session.append("user", "text", message)
                reply = ("修改系统结构需要管理员权限（当前角色无权触发）。\n"
                         "你可以继续查询/登记现有数据；需要加字段请联系管理员。")
                session.append("assistant", "text", reply)
                return {"reply": reply}
            session.append("user", "text", message)
            return self._present_amend_plan(amend, session)

        # idle：非加字段、非生成意图 → 交回业务模式（不落盘，业务图自己落）
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
            from syscraft.errors import user_message
            session.set_slot("gen_state", None)
            # 已知错误类型透传原文，未分类的收敛成模糊文案——异常字符串
            # 可能带路径与堆栈摘要，不该进会话历史
            reply = f"需求解析失败：{user_message(error)}"
            session.append("assistant", "error", {"message": reply})
            return {"reply": reply}

        if result.needs_clarification:
            session.set_slot("gen_state", "awaiting_answers")
            lines = "\n".join(f"{i}. {q.question}（默认：{q.default}）"
                              for i, q in enumerate(result.questions, 1))
            reply = f"开始建模前有几个问题（直接回复答案即可，或回复「按默认」跳过）：\n{lines}"
            # 追问走结构化事件：前端渲染成表单卡（逐项填写 / 一键按默认），
            # 答案仍以普通消息回传——抽取层不感知界面形态，两轮协议不变
            content = {"kind": "clarify", "text": reply,
                       "items": [{"question": q.question, "default": q.default}
                                 for q in result.questions],
                       "options": [{"label": "全部按默认", "value": "按默认"}]}
            session.append("assistant", "ask", content)
            return {"reply": reply, "event": {"type": "ask", **content}}

        return self._present_plan(result, session)

    def _present_plan(self, result, session) -> dict:
        """把抽取结果落成一份待确认的计划。

        版本号不在这里分配：抽取阶段不持生成锁，两个会话并发描述需求时会算到
        同一个 vN、写同一个 auto_gen.vN.json，后写覆盖前写——先确认的人跑的是
        别人的模型。版本号改到 _run_pipeline 持锁后分配，计划文件用 plan_id 隔离。
        """
        model = result.model
        plan_id = uuid.uuid4().hex[:12]
        model["model_version"] = ""  # 确认时分配，见 _run_pipeline
        model_path = config.MODELS_DIR / f"plan.{plan_id}.json"
        model_path.write_text(json.dumps(model, ensure_ascii=False, indent=2), encoding="utf-8")
        self._sweep_plans()

        session.set_slot("gen_model_path", str(model_path))
        session.set_slot("gen_state", "awaiting_plan")
        session.set_slot("gen_mode", "create")
        entities = "、".join(e["display_name"] for e in model["entities"])
        reply = (f"需求已理解，将生成{len(model['entities'])}个实体：{entities}。\n\n"
                 f"{result.plan}\n\n确认无误请回复「确认生成」（版本号在确认时分配），要调整直接重新描述。")
        content = {"summary": reply, "version": "", "assumptions": result.assumptions}
        session.append("assistant", "plan", content)
        # 计划以 plan 事件回传：流式过程中前端就渲染成计划卡（带确认按钮），
        # 而不是等刷新后从历史里才看到
        return {"reply": reply, "event": {"type": "plan", **content}}

    def _present_amend_plan(self, request, session) -> dict:
        """增量修改的计划：在在线版本模型上追加一个字段，确认后走迁移流水线。

        与全量生成的差别只有两点：模型来源是「在线版本的模型原件」（不是重新抽取），
        流水线打开 allow_migrate（对已存在的表 ALTER ADD COLUMN）。其余步骤完全复用——
        同一套编译、验收、回滚，增量才不需要第二套质量保证。
        """
        from syscraft.requirement.amend import append_field, build_field, load_online_model
        from syscraft.runtime.switcher import current_version

        version = current_version()
        model, _source = load_online_model(version)
        if model is None:
            reply = (f"当前在线版本 {version or '未知'} 没有留下模型原件，无法做字段级增量。\n"
                     "可以整版重新描述需求生成。")
            session.append("assistant", "text", reply)
            return {"reply": reply}
        entity = next((e for e in model.get("entities", [])
                       if e["entity"] == request.entity.entity), None)
        if entity is None:
            reply = f"在线版本里没有「{request.entity.display_name}」这张表，无法加字段。"
            session.append("assistant", "text", reply)
            return {"reply": reply}

        existing = {f["column"] for f in entity.get("fields", [])}
        spec = build_field(request.field_text, existing)
        problem = append_field(model, entity["entity"], spec)
        if problem:
            session.append("assistant", "text", problem)
            return {"reply": problem}

        plan_id = uuid.uuid4().hex[:12]
        model["model_version"] = ""  # 确认时分配
        plan_path = config.MODELS_DIR / f"plan.{plan_id}.json"
        plan_path.write_text(json.dumps(model, ensure_ascii=False, indent=2), encoding="utf-8")
        self._sweep_plans()

        session.set_slot("gen_model_path", str(plan_path))
        session.set_slot("gen_state", "awaiting_plan")
        session.set_slot("gen_mode", "amend")
        type_label = spec.type
        if spec.length:
            type_label += f"({spec.length})"
        elif spec.precision:
            type_label += f"({spec.precision},{spec.scale})"
        reply = (f"将在「{entity['display_name']}」新增字段：{spec.semantic}"
                 f"（列名 {spec.column}，类型 {type_label}，允许为空，存量数据不受影响）。\n\n"
                 "确认后执行：ALTER TABLE 加列 → 新版本重渲染 → 编译 → 上线 → 自动验收，约 2 分钟。\n"
                 "确认请回复「确认生成」；取消回复「取消」。")
        content = {
            "summary": reply, "version": "", "kind": "amend",
            "field": {"column": spec.column, "type": type_label, "semantic": spec.semantic},
        }
        session.append("assistant", "plan", content)
        return {"reply": reply, "event": {"type": "plan", **content}}

    @staticmethod
    def _sweep_plans(max_age_seconds: int = 86400) -> None:
        """清掉一天前没人确认的计划文件。误触生成意图会留 plan.*.json，
        不回收的话 models/ 会变成垃圾场（版本号扫描也会越来越慢）。"""
        cutoff = time.time() - max_age_seconds
        for path in config.MODELS_DIR.glob("plan.*.json"):
            try:
                if path.stat().st_mtime < cutoff:
                    path.unlink(missing_ok=True)
            except OSError:
                continue

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
        # 锁一旦拿到，初始化段的任何异常都必须自己释放：否则全局锁永久占用，
        # 全平台 /api/chat 从此 503，只能重启进程（审查 P0：模型文件损坏即触发）。
        # 用 BaseException：暂停是通过 task.cancel() 打断派发的，CancelledError 不是
        # Exception——漏掉它会在「锁已拿到、gen_state 还没置 running」的窗口里泄漏锁
        try:
            model = json.loads(model_path.read_text(encoding="utf-8"))
            amend_mode = session.get_slot("gen_mode") == "amend"
            version = str(model.get("model_version") or "")
            if not version:
                version = _next_version()
                model["model_version"] = version
                # 计划文件转正：版本号进内容也进文件名，审计与手工复跑都靠它
                target = config.MODELS_DIR / f"auto_gen.{version}.json"
                target.write_text(json.dumps(model, ensure_ascii=False, indent=2), encoding="utf-8")
                model_path.unlink(missing_ok=True)
                model_path = target
                session.set_slot("gen_model_path", str(model_path))
            session.set_slot("gen_state", "running")
            reply = f"开始生成（版本 {version}），全程约 2 分钟，进度见下方事件。"
            session.append("assistant", "text", reply)

            try:
                me = session.meta().get("user_id")
                self._running_owner = int(me) if me is not None else None
            except (TypeError, ValueError):
                self._running_owner = None
            self._cancel.clear()
        except BaseException:
            generation_lock.release()
            session.set_slot("gen_state", None)
            session.set_slot("gen_mode", None)
            raise

        async def task():
            try:
                await self._stop_old_product(emit)
                emit({"type": "phase", "step": "流水线开始", "ok": True, "detail": f"版本 {version}"})
                pipeline = Pipeline(
                    notify=lambda name, ok, detail: emit(
                        {"type": "phase", "step": name, "ok": ok, "detail": detail}),
                    keep_running=True,
                    should_cancel=self._cancel.is_set,
                    on_event=emit,
                    allow_migrate=amend_mode)
                result = await asyncio.to_thread(pipeline.run, model_path)
                if result.ok:
                    from syscraft.requirement.model_catalog import reset_catalog_cache
                    reset_catalog_cache()  # 新实体要让业务模式的工具白名单看见
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
                from syscraft.errors import user_message
                reply = f"生成异常中断：{user_message(error)}"
                session.append("assistant", "error", {"message": reply})
                emit({"type": "error", "message": reply})
            finally:
                generation_lock.release()
                self._running_owner = None
                self._cancel.clear()
                session.set_slot("gen_state", None)
                session.set_slot("gen_mode", None)

        pipeline_task = asyncio.create_task(task())
        # asyncio 只对运行中的 task 持弱引用：不保存句柄，任务可能在运行中被 GC，
        # finally 里的锁释放与状态清理随之全部丢失（全局锁永久占用，只能重启）
        self._pipeline_task = pipeline_task
        pipeline_task.add_done_callback(_log_pipeline_crash)
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


def _log_pipeline_crash(task: asyncio.Task) -> None:
    """done 兜底：task() 内部已把异常落进会话，这里只接「finally 清场段本身炸了」
    的最后一道——静默消失的生成比失败的生成更难排查。"""
    if task.cancelled():
        return
    error = task.exception()
    if error is not None:
        print(f"[generate] 流水线收尾异常：{error!r}", flush=True)


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
