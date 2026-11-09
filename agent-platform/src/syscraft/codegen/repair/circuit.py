"""两层六道熔断（《代码生成器设计》6.7）。

step / turn 两级由循环本身计数；本文件管跨 step / 跨 turn 的四道：
错误指纹（连续 2 个 turn 结束时错误相同 → 模板缺陷，重试无用）、
无进展（状态指纹连续 3 步不变 → 模型在空转）、时长（15 分钟，演示保护）、token（8 万，成本控制）。

分层的意义在诊断：step 用尽 = 卡在某一步，turn 用尽 = 方向就错了，
错误指纹 = 根因不在代码（该找开发者改模板）。三种情况的用户建议完全不同。
"""

from __future__ import annotations

import hashlib
import time
from pathlib import Path

DEADLINE_SECONDS = 15 * 60
TOKEN_BUDGET = 80_000
NO_PROGRESS_STEPS = 3
FINGERPRINT_TURNS = 2


def signature(errors: str) -> str:
    """编译错误的指纹：只取报错文件名的集合。

    行号会随一处修改整体漂移，错误文案随编译器措辞变化——两者都不该参与判定。
    文件级骨架偏粗（同文件换了个错误也算相同），宁可早停也别让用户等三轮注定失败的修复。
    """
    skeleton = sorted({
        line.split(":")[0].strip()
        for line in (errors or "").splitlines()
        if ".java" in line and ":" in line
    })
    return hashlib.sha256(("\n".join(skeleton) or errors[:200]).encode()).hexdigest()[:16]


class CircuitBreaker:
    def __init__(self, version_dir: Path, deadline_seconds: int = DEADLINE_SECONDS,
                 token_budget: int = TOKEN_BUDGET):
        self.started = time.monotonic()
        self.deadline_seconds = deadline_seconds
        self.token_budget = token_budget
        self.tokens_used = 0
        self._turn_end_signatures: list[str] = []
        self._state_history: list[str] = []

    def tokens(self, used: int) -> None:
        self.tokens_used += used

    def record_step_state(self, version_dir: Path) -> None:
        """每个**编辑尝试**结束后调用（读文件不算）：拍当前 generated 源码的指纹。
        连续多次编辑都没能改变工程状态 → 模型在空转。"""
        digest = hashlib.sha256()
        for path in sorted((version_dir / "src/main/java/com/generated/generated").rglob("*.java")):
            digest.update(str(path.relative_to(version_dir)).encode())
            digest.update(path.read_bytes())
        self._state_history.append(digest.hexdigest()[:16])

    def record_turn_end(self, errors: str) -> str | None:
        """turn 结束时核对错误指纹；触发返回原因。

        errors 为空（这一轮根本没跑过编译）不算证据：空串的指纹恒等，
        会把「模型还在读文件摸索」误判成「模板缺陷」。
        """
        if not errors.strip():
            return None
        self._turn_end_signatures.append(signature(errors))
        if len(self._turn_end_signatures) >= FINGERPRINT_TURNS and \
                len(set(self._turn_end_signatures[-FINGERPRINT_TURNS:])) == 1:
            return "错误指纹连续两轮相同：重试无用，多半是模板缺陷，请找开发者改模板"
        return None

    def check(self) -> str | None:
        if time.monotonic() - self.started > self.deadline_seconds:
            return "修复超时（15 分钟）：演示保护触发"
        if self.tokens_used > self.token_budget:
            return "token 超出预算（8 万）：成本控制触发"
        if len(self._state_history) >= NO_PROGRESS_STEPS and \
                len(set(self._state_history[-NO_PROGRESS_STEPS:])) == 1:
            return "连续多步工程状态未变：模型在空转"
        return None
