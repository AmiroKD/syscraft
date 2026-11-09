"""需求解析器（《需求到模式映射设计》3 节的处理流水线）。

① 解析（LLM 抽取候选）→ ② 澄清（≤3 问、必带默认值、允许「你看着办」）→
③ 建模（中间表示 → 校验 → 业务模型）→ ④ 生成计划（人类可读，交用户确认）。

与《代码生成器设计》6.8 同一个适配思路：LLM 只产出受限 JSON，
实体/字段/规则形态全部在平台校验；解析不出来就追问，追问不出就按默认值走
（4.3 节：允许放弃回答，按默认值生成并明确回述）。
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Callable

from syscraft import config
from syscraft.requirement import schema_map

EXTRACT_PROMPT_TEMPLATE = """你是业务系统的需求分析师。从用户需求里抽取业务模型（中间表示 JSON）。

字段类型只允许：VARCHAR/TEXT/INT/BIGINT/DECIMAL/DATE/DATETIME/BOOLEAN。
业务规则只允许三种形态（跨表规则如库存扣减不支持，写进 assumptions）：
- computed: {"type":"computed","target":"实体.字段","expr":"字段 = 字段 * 字段"}
- constraint: {"type":"constraint","target":"实体.字段","expr":"字段 > 0"}
- state: {"type":"state","field":"实体.字段","flow":["态1","态2"],"initial":"态1"}

实体名/字段名：小写字母开头，小写字母/数字/下划线，不能用数据库保留字（如 order/user）。
中文名放 displayName 与 semantic。表名不能用 adm_ 前缀。
每个实体的列里不要放 id 和 created_at（系统自动注入）。
一对多用子表外键列表达（如 order_item.product_id），并在 relations 里声明：
{"from":"order_item.product_id","to":"product.id","type":"MANY_TO_ONE"}
建议角色 roles 里只给 {name, displayName, level:10, grants}，层级不许超过 10。
推断出来的决策写进 assumptions（如「金额按未税计算」）。

输出 JSON，二选一：

信息不足有歧义（最多 3 个问题，每个必带默认值）：
{"needClarification": true, "questions": [{"question": "...", "default": "我的默认理解"}]}

可以建模（列数组键名必须是 columns；列内键：name/type/nullable/unique/length/semantic）：
{"entities": [{"name": "...", "displayName": "...", "columns": [{"name": "...", "type": "VARCHAR", "nullable": false, "semantic": "..."}]}], "relations": [...], "rules": [...], "roles": [...], "assumptions": [...]}

已确认的澄清结论（必须遵守，不得再问）：
{answers}

用户需求：
{requirement}"""

_FENCE = re.compile(r"```(?:json)?\s*(.*?)\s*```", re.DOTALL)

# 指令注入特征（P1-2c）：需求文本是不可信输入，拼接进 prompt 前先过一道启发式检测。
# 命中不意味真的能提权（输出受限 JSON + 权限收敛在平台侧），但必须拦下并留痕——
# 否则威胁模型里「用户输入当数据」这句话没有执行点
_INJECTION_PATTERNS = (
    re.compile(r"忽略(以上|上面|前面|之前|所有|全部)*(的)?(指令|提示|规则|要求|设定)"),
    re.compile(r"(无视|不顾|不要管)(以上|上面|前面|之前|所有|全部)*(的)?(指令|提示|规则|要求)"),
    re.compile(r"ignore\s+(all\s+)?(previous|above|prior)\s+instructions", re.IGNORECASE),
    re.compile(r"你现在是?(一个)?(不受限|无限制|没有限制|越狱)", re.IGNORECASE),
    re.compile(r"(系统提示|system\s*prompt)"),
    re.compile(r"(DAN|越狱模式|开发者模式)", re.IGNORECASE),
)


def detect_injection(requirement: str) -> str | None:
    """返回命中的注入特征描述（None = 未命中）。"""
    text = requirement or ""
    for pattern in _INJECTION_PATTERNS:
        match = pattern.search(text)
        if match:
            return match.group(0)
    return None


@dataclass
class Question:
    question: str
    default: str


@dataclass
class ExtractResult:
    """一次抽取的结果：要么追问，要么模型 + 生成计划。"""

    questions: list[Question] = field(default_factory=list)
    model: dict | None = None
    plan: str = ""
    assumptions: list[str] = field(default_factory=list)

    @property
    def needs_clarification(self) -> bool:
        return bool(self.questions)


class RequirementExtractor:
    def __init__(self, llm_call: Callable[[list[dict]], str] | None = None,
                 existing_entities: set[str] | None = None):
        self._llm_call = llm_call or self._http_call
        self._existing = existing_entities or set()

    # ------------------------------------------------------------------ 入口

    def extract(self, requirement: str, answers: list[str] | None = None) -> ExtractResult:
        """answers 非空 = 第二轮：把用户对追问的回答带给模型，不再追问。"""
        injected = detect_injection(requirement)
        if injected:
            raise schema_map.ModelValidationError(
                [f"需求描述里包含疑似指令注入的内容（{injected!r}）。"
                 "请用自然语言描述业务需求，不要在需求里给系统下指令。"])
        messages = self._messages(requirement, answers or [])
        content = self._llm_call(messages)
        try:
            parsed = self._parse(content)
        except ValueError as parse_error:
            # JSON 损坏（截断/多了字）同样走自修正：把错误原文喂回去重出一次
            messages.append({"role": "assistant", "content": content})
            messages.append({"role": "user", "content":
                             f"你的输出不是合法 JSON（{parse_error}）。"
                             "重新输出完整 JSON，不要任何解释或代码围栏。"})
            content = self._llm_call(messages)
            parsed = self._parse(content)

        if parsed.get("needClarification") and not answers:
            questions = [Question(question=str(q.get("question", "")), default=str(q.get("default", "")))
                         for q in parsed.get("questions", []) if isinstance(q, dict)]
            if not questions:
                questions = [Question(question="请把需求描述得更具体一些。", default="按常见做法处理")]
            return ExtractResult(questions=questions[:3])  # 4.2 条一：一次不超过三个问题

        if parsed.get("needClarification") and answers:
            # 用户已经回答过（或放弃回答）：4.3 条三——按默认值生成并回述，不许再问
            messages.append({"role": "assistant", "content": content})
            messages.append({"role": "user", "content":
                             "用户已放弃回答。按你在 questions 里给出的默认值直接建模，"
                             "输出完整 JSON（不要 needClarification），并把默认值写进 assumptions。"})
            content = self._llm_call(messages)
            parsed = self._parse(content)
            if parsed.get("needClarification"):
                raise schema_map.ModelValidationError(
                    ["用户放弃回答后模型仍拒绝建模，请换个说法描述需求"])

        ir = schema_map.normalize(parsed)
        problems = schema_map.validate(ir, existing_entities=self._existing)
        if problems:
            # 校验失败喂回模型自修正一次（设计 3 节 ④ 校验的容错路径）
            messages.append({"role": "assistant", "content": content})
            messages.append({"role": "user", "content":
                             "你的输出有以下问题，修正后重新输出完整 JSON：\n- " + "\n- ".join(problems[:8])})
            content = self._llm_call(messages)
            ir = schema_map.normalize(self._parse(content))
            problems = schema_map.validate(ir, existing_entities=self._existing)
            if problems:
                raise schema_map.ModelValidationError(problems)

        model = schema_map.to_business_model(ir, model_version="v1")
        return ExtractResult(model=model, plan=_plan(model, ir),
                             assumptions=list(model.get("assumptions", [])))

    # ------------------------------------------------------------------ 内部

    def _messages(self, requirement: str, answers: list[str]) -> list[dict]:
        # str.format 会把 JSON 花括号当占位符，所以手动替换
        entities = self._existing_desc()
        answers_block = "\n".join(f"- {answer}" for answer in answers) or "（无）"
        system = (EXTRACT_PROMPT_TEMPLATE
                  .replace("{answers}", answers_block)
                  .replace("{requirement}", requirement)
                  .replace("{entities}", entities))
        return [{"role": "system", "content": system},
                {"role": "user", "content": requirement}]

    def _existing_desc(self) -> str:
        if not self._existing:
            return "（系统里还没有任何实体）"
        return "已存在（不得重名）：" + "、".join(sorted(self._existing))

    def _http_call(self, messages: list[dict]) -> str:
        response = httpx_post(messages)
        if response.status_code != 200:
            raise RuntimeError(f"LLM HTTP {response.status_code}")
        return response.json()["choices"][0]["message"]["content"] or ""

    def _parse(self, content: str) -> dict:
        text = (content or "").strip()
        match = _FENCE.search(text)
        if match:
            text = match.group(1)
        start, end = text.find("{"), text.rfind("}")
        if start < 0 or end <= start:
            raise ValueError("LLM 输出里没有 JSON")
        return json.loads(text[start:end + 1])


def httpx_post(messages: list[dict]):
    import httpx
    return httpx.post(
        f"{config.LLM_BASE_URL}/chat/completions",
        headers={"Authorization": f"Bearer {config.LLM_API_KEY}"},
        # 多实体模型的 JSON 很大，1024 会被截成非法 JSON（实测）
        json={"model": config.LLM_MODEL, "messages": messages,
              "max_tokens": max(config.LLM_MAX_TOKENS, 4096), "temperature": 0},
        timeout=config.LLM_TIMEOUT_SECONDS,
    )


def _plan(model: dict, ir: dict) -> str:
    """生成计划（6 节）：人类可读，交用户确认。"""
    lines = ["将生成："]
    for entity in model["entities"]:
        computed = {r["target"] for r in entity["rules"]["computed"]}
        states = {r["field"] for r in entity["rules"]["states"]}
        fields = "、".join(
            f"{f['semantic']}"
            f"{'（唯一）' if f.get('unique') and not f.get('primary_key') else ''}"
            f"{'（自动计算）' if f['column'] in computed else ''}"
            f"{'（状态机）' if f['column'] in states else ''}"
            for f in entity["fields"] if not f.get("primary_key"))
        lines.append(f"- {entity['display_name']}（{entity['entity']}，{len(entity['fields'])} 字段）：{fields}")
    user_grants = [g["entity"] for g in model.get("grants", []) if g["role"] == "USER"]
    if user_grants:
        lines.append(f"权限默认：内置 USER 角色对 {', '.join(user_grants)} 只读；写入权由管理员显式上调。")
    assumptions = model.get("assumptions") or []
    if assumptions:
        lines.append("我将按 " + "、".join(assumptions) + " 理解，若有出入请指出。")
    return "\n".join(lines)
