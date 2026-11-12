"""五个修复工具与三段管道（《代码生成器设计》6.4/6.6）。

pre-execute：路径白名单、保护路径、old_string 唯一性——策略问题，拒绝并回给模型重试；
execute：实际读写、编译；
post-execute：语法预检（括号配对）、计算 diff——模型手误，当软错误退回，不落成编译失败。

三条拒绝语义在日志里分得清：pre 拒绝是策略问题，post 失败是模型手误，
execute 异常是环境问题（《代码生成器设计》6.6 的审计收益）。
"""

from __future__ import annotations

import re
import shutil
import subprocess
from pathlib import Path

BUILD_TIMEOUT_SECONDS = 300
MAX_READ_LINES = 200
MAX_PATTERN_LEN = 200
MAX_GREP_HITS = 40


def syntax_precheck(text: str) -> str | None:
    """低级语法检查：去掉字符串与注释后，花括号/圆括号必须配平。

    只挡「少个括号」这类几十毫秒能发现的问题，类型错误仍靠编译——
    预检做重了误报一次的成本比编译一次还高。
    """
    stripped = re.sub(r'"(?:\\.|[^"\\])*"', '""', text)
    stripped = re.sub(r"'(?:\\.|[^'\\])*'", "''", stripped)
    stripped = re.sub(r"//[^\n]*", "", stripped)
    stripped = re.sub(r"/\*.*?\*/", "", stripped, flags=re.DOTALL)
    for opens, closes in (("{", "}"), ("(", ")")):
        if stripped.count(opens) != stripped.count(closes):
            return f"括号不配对：{opens}{closes}"
    return None


class ToolExec:
    """
    :param build_runner: 可注入的编译函数（返回 (ok, output)）。测试用它替真 mvn。
    """

    def __init__(self, version_dir: Path, build_runner=None):
        self.root = version_dir.resolve()
        self.write_root = self.root / "src/main/java/com/generated/generated"
        self.build_runner = build_runner or self._default_build

    # ------------------------------------------------------------- 工具入口

    def run(self, action: dict) -> dict:
        """三段管道。返回 {ok, brief, output?, diff?, error?}，永不抛异常。"""
        tool = action.get("tool")
        handler = {
            "read_file": self._read_file,
            "edit_file": self._edit_file,
            "grep": self._grep,
            "list_files": self._list_files,
            "run_build": self._run_build,
        }.get(tool)
        if handler is None:
            return {"ok": False, "error": f"未知工具：{tool}",
                    "brief": f"unknown tool {tool}"}
        try:
            return handler(action)
        except Exception as error:  # 环境问题也不许打断循环，记下来交给模型面对
            return {"ok": False, "error": f"工具执行异常：{error}", "brief": f"{tool} crashed"}

    # ------------------------------------------------------------- pre 守卫

    def _resolve(self, action: dict, for_write: bool) -> Path | str:
        raw = str(action.get("path", ""))
        path = (self.root / raw).resolve()
        # 逃逸检查：../ 与软链都可能跑出版本目录。必须用 is_relative_to 而不是
        # startswith——字符串前缀匹配会把 .../v10 当成 .../v1 的子目录（v1 与
        # v10 是兄弟版本，读穿就是跨版本泄露）
        if not path.is_relative_to(self.root):
            return "路径越出版本目录"
        if for_write:
            if not path.is_relative_to(self.root / "src"):
                return "写入只允许 src/ 之内"
            if path.is_relative_to(self.root / "src/main/java/com/generated/platform") or \
                    path.name == "pom.xml":
                # platform 与 pom 是「版本目录 = 完整工程」的地基，修复循环只读（禁令 D6）
                return "platform 与 pom.xml 是保护路径，只读"
            if not path.is_relative_to(self.write_root):
                return "写入只允许 generated/ 之内"
        return path

    # ------------------------------------------------------------- 工具实现

    def _read_file(self, action: dict) -> dict:
        resolved = self._resolve(action, for_write=False)
        if isinstance(resolved, str):
            return {"ok": False, "error": resolved, "brief": "read rejected"}
        path = resolved
        if not path.is_file():
            return {"ok": False, "error": f"文件不存在：{path.name}", "brief": "read missing"}
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
        start = max(int(action.get("start_line") or 1), 1)
        end = min(int(action.get("end_line") or start + MAX_READ_LINES - 1), len(lines), start + MAX_READ_LINES - 1)
        body = "\n".join(f"{n:5d}  {lines[n - 1]}" for n in range(start, end + 1))
        return {"ok": True, "output": body or "（空文件）",
                "brief": f"read {path.name} L{start}-{end}"}

    def _edit_file(self, action: dict) -> dict:
        resolved = self._resolve(action, for_write=True)
        if isinstance(resolved, str):
            return {"ok": False, "error": resolved, "brief": "edit rejected"}
        path = resolved
        old = action.get("old_string") or ""
        new = action.get("new_string") or ""
        if not path.is_file():
            return {"ok": False, "error": f"文件不存在：{path.name}", "brief": "edit missing"}
        text = path.read_text(encoding="utf-8")
        # 唯一性是这套设计里最重要的一条约束：逼模型先读真文件再动手，
        # 一条约束同时挡住改错文件、改错位置、凭想象写代码三类失败
        count = text.count(old)
        if count == 0:
            return {"ok": False, "error": "old_string 未找到（先 read_file 看真实内容）", "brief": "edit no-match"}
        if count > 1:
            return {"ok": False, "error": f"old_string 匹配到 {count} 处，必须唯一", "brief": "edit ambiguous"}

        before = text
        after = text.replace(old, new, 1)
        # 写前快照：失败可人工还原
        backup = path.with_suffix(path.suffix + ".bak")
        shutil.copy2(path, backup)
        path.write_text(after, encoding="utf-8")

        problem = syntax_precheck(after)
        if problem:
            path.write_text(before, encoding="utf-8")  # 软错误：回滚本次编辑
            return {"ok": False, "error": f"语法预检失败（已回滚）：{problem}", "brief": "edit syntax"}

        return {"ok": True, "brief": f"edit {path.name}",
                "diff": _diff_lines(before, after), "snapshot": str(backup)}

    def _grep(self, action: dict) -> dict:
        pattern = str(action.get("pattern", ""))
        if not pattern or len(pattern) > MAX_PATTERN_LEN:
            return {"ok": False, "error": "pattern 为空或超长（上限 200 字符）", "brief": "grep rejected"}
        try:
            regex = re.compile(pattern)
        except re.error as error:
            return {"ok": False, "error": f"正则不合法：{error}", "brief": "grep rejected"}

        hits: list[str] = []
        src = self.root / "src/main/java"
        for path in sorted(src.rglob("*.java")):
            for number, line in enumerate(path.read_text(encoding="utf-8", errors="replace").splitlines(), 1):
                if regex.search(line):
                    rel = path.relative_to(self.root)
                    hits.append(f"{rel}:{number}: {line.strip()[:160]}")
                    if len(hits) >= MAX_GREP_HITS:
                        break
            if len(hits) >= MAX_GREP_HITS:
                break
        return {"ok": True, "output": "\n".join(hits) or "（无匹配）",
                "brief": f"grep {pattern!r} -> {len(hits)} hits"}

    def _list_files(self, action: dict) -> dict:
        sub = str(action.get("dir") or "")
        target = (self.root / sub).resolve()
        # 同 _resolve：is_relative_to，防 v1/v10 这类前缀兄弟目录被读穿
        if not target.is_relative_to(self.root) or not target.is_dir():
            return {"ok": False, "error": "目录不存在或越界", "brief": "list rejected"}
        entries = sorted(p.relative_to(self.root).as_posix() for p in target.iterdir())
        return {"ok": True, "output": "\n".join(entries) or "（空）", "brief": f"list {sub or '.'}"}

    def _run_build(self, action: dict) -> dict:
        # run_build 不接受任何参数：也就没有命令注入面（设计 6.4）
        ok, output = self.build_runner()
        return {"ok": ok, "output": output, "brief": "build " + ("ok" if ok else "failed")}

    def _default_build(self) -> tuple[bool, str]:
        process = subprocess.run(
            [str(_maven()), "-B", "-q", "package", "-DskipTests"],
            cwd=self.root, capture_output=True, text=True, timeout=BUILD_TIMEOUT_SECONDS,
        )
        output = (process.stdout + process.stderr)[-4000:]
        return process.returncode == 0, output


def _maven() -> Path:
    from shutil import which
    found = which("mvn")
    if found:
        return Path(found)
    # 本机 Maven 不在 PATH，用 wrapper 解压出来的那份（与 README 的启动说明一致）
    return Path.home() / ".m2/wrapper/dists/apache-maven-3.9.12/57e1dc81/bin/mvn"


def _diff_lines(before: str, after: str) -> str:
    before_lines, after_lines = before.splitlines(), after.splitlines()
    keep = 0
    for a, b in zip(before_lines, after_lines):
        if a != b:
            break
        keep += 1
    removed = before_lines[keep:keep + 6]
    added = after_lines[keep:keep + 6]
    parts = [f"- {line}" for line in removed] + [f"+ {line}" for line in added]
    return "\n".join(parts[:12])
