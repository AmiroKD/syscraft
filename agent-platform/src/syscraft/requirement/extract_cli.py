"""需求抽取 CLI：

    PYTHONPATH=src .venv/bin/python -m syscraft.requirement.extract_cli \
        --text "一段需求" [--answers answer1;answer2] [--out models/xxx.json] [--version v3]
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from syscraft.requirement.extractor import RequirementExtractor
from syscraft.requirement.model_catalog import catalog


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="需求 → 业务模型")
    parser.add_argument("--text", required=True)
    parser.add_argument("--answers", default="", help="对追问的回答，分号分隔")
    parser.add_argument("--out", help="模型 JSON 输出路径")
    parser.add_argument("--version", default="v1", help="model_version（写进模型 JSON）")
    args = parser.parse_args(argv)

    existing = {e.entity for e in catalog().all()}
    extractor = RequirementExtractor(existing_entities=existing)
    result = extractor.extract(args.text, answers=[a for a in args.answers.split(";") if a])

    if result.needs_clarification:
        print(json.dumps({"needClarification": True,
                          "questions": [{"question": q.question, "default": q.default}
                                        for q in result.questions]}, ensure_ascii=False, indent=2))
        return 2

    print(result.plan)
    if args.out:
        out = Path(args.out)
        out.parent.mkdir(parents=True, exist_ok=True)
        model = dict(result.model)
        model["model_version"] = args.version
        out.write_text(json.dumps(model, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"\n模型已写出：{out}（配 --version {args.version}）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
