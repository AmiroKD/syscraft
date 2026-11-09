"""上下文投影（《代码生成器设计》6.5）。

存储不压缩，投影才裁剪：日志永远完整，送进模型的永远只有几千 token。
近端（最近 5 个事件）给全量，远端压成一行摘要；编译错误单独全文给出；
已改清单单列一段，避免模型重复改同一处。
"""

from __future__ import annotations

import re
from pathlib import Path

from syscraft.codegen.repair.events import Event, EventLog

RECENT_FULL = 5
READ_LIMIT = 5
_ERROR_LOCATION = re.compile(r"([\w/.-]+\.java):\[?(\d+)")


def project(log: EventLog, version_dir: Path, turn: int, step: int,
            max_turns: int, max_steps: int) -> str:
    events = log.all()
    lines: list[str] = [
        f"你是 Java 编译错误修复器。工程目录：{version_dir}",
        f"当前第 {turn}/{max_turns} 轮、第 {step}/{max_steps} 步。每一步只做一个动作。",
        "",
        "可用动作（输出一个 JSON 对象，不要其它内容）：",
        '{"tool":"read_file","path":"相对路径","start_line":1,"end_line":200}',
        '{"tool":"edit_file","path":"相对路径","old_string":"必须唯一匹配的原文","new_string":"替换后"}',
        '{"tool":"grep","pattern":"正则"}',
        '{"tool":"list_files","dir":"相对目录"}',
        '{"tool":"run_build"}',
        "",
        "规则：只允许修改 src/main/java/com/generated/generated/ 下的文件；"
        "platform 与 pom.xml 只读；edit 前 必须 read_file 看真实内容，old_string 必须唯一；"
        "编译通过前不要宣称修好——用 run_build 验证；"
        "不要重复已经做过且无新收获的动作（尤其是对同一文件的同参数读取）,"
        "信息够了就直接 edit_file 尝试修复。",
        "",
        *_error_headline(log),
        "=== 最近一次编译错误 ===",
        (log.last("CompileFailed").payload.get("errors") or "")[-2500:] if log.last("CompileFailed") else "（尚无）",
        "",
        "=== 已改清单 ===",
    ]

    edits = [e for e in events if e.type == "EditFile" and e.payload.get("ok")]
    if edits:
        seen: dict[str, int] = {}
        for event in edits:
            name = event.payload.get("path", "?")
            seen[name] = seen.get(name, 0) + 1
        lines += [f"- {name} ×{count}" for name, count in seen.items()]
    else:
        lines.append("- （尚未修改任何文件）")

    lines.append("")
    lines.append("=== 事件轨迹（近端全量，远端摘要）===")
    recent: set[int] = set()
    for event in events[-RECENT_FULL:]:
        recent.add(event.seq)
        lines.append(_full(event))
    remote = [e for e in events if e.seq not in recent]
    if remote:
        lines.append("……更早：")
        lines += ["  " + e.summary() for e in remote]
    return "\n".join(lines)


def _full(event: Event) -> str:
    payload = dict(event.payload)
    output = payload.pop("output", None)
    diff = payload.pop("diff", None)
    errors = payload.pop("errors", None)
    head = f"#{event.seq} {event.type} " + " ".join(f"{k}={str(v)[:80]}" for k, v in payload.items())
    parts = [head]
    if diff:
        parts.append(diff)
    if output:
        parts.append(str(output)[:1500])
    if errors:
        parts.append(str(errors)[:1500])
    return "\n".join(parts)


def _error_headline(log: EventLog) -> list[str]:
    """把编译错误里的位置提炼成显式指引，并对只读兜圈的行为设禁令。

    实测（deepseek-flash）：错误文本里明明有 文件:行号，模型仍会反复 read 别处；
    把「先看哪、别再读」写成指令比指望模型自己归纳可靠得多。
    """
    compile_event = log.last("CompileFailed")
    if compile_event is None:
        return []
    errors = str(compile_event.payload.get("errors") or "")
    lines: list[str] = []
    match = _ERROR_LOCATION.search(errors)
    if match:
        file_name, line_no = match.group(1).split("/")[-1], int(match.group(2))
        lines.append(f"错误位置：{file_name} 第 {line_no} 行附近。"
                     f"第一步建议 read_file 该文件 {max(line_no - 25, 1)}-{line_no + 25} 行。")
    read_count = sum(1 for e in log.all() if e.type == "ReadFile")
    if read_count >= READ_LIMIT:
        lines.append(f"你已读取 {read_count} 次——信息足够，**禁止再 read_file/list_files/grep**，"
                     "下一步必须 edit_file 做最小修改（哪怕只是你当前最有把握的一处），然后 run_build。")
    return lines
