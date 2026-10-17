"""用 Claude API 依 taxonomy.yaml 批次標記每題的主題（topic）與標籤（tags）。

用法：
    export ANTHROPIC_API_KEY=...
    python -m pipeline.tag                       # 標記所有還沒有 topic 的題目
    python -m pipeline.tag --subject NEURO --limit 40   # 先小量試跑，看 data/review/review_queue.csv 滿不滿意
    python -m pipeline.tag --force --only 113-1/ORTHO    # 重標某科
    python -m pipeline.tag --apply-overrides     # 套用 data/review/topic_overrides.csv（人工改過的答案，永遠優先）
    python -m pipeline.tag --remap OLD=NEW        # 分類表改 code 後批次改名

輸出直接寫回 data/json/*.json 的 topic / tags / topic_confidence / topic_source 欄位。
信心 < TAG_LOW_CONFIDENCE 的題目會在 validate 時進審核清單。

人工覆寫檔 data/review/topic_overrides.csv 欄位：id,topic,tags（tags 用 | 分隔，可留空）
標記過的題目 topic_source="llm"；人工覆寫後 topic_source="human"，之後 --force 也不會動它。
"""
from __future__ import annotations

import argparse
import csv
import json
import sys
import time
from pathlib import Path

import yaml

from . import config as C

SYSTEM_PROMPT = """你是台灣物理治療師國家考試的資深出題委員與教師。
你會收到同一科目的多道選擇題，以及這一科的主題分類表（code、名稱、關鍵字）。
請替每一題選出「最主要」考的一個主題 code，只能從給定清單選。判斷依據是題目在考什麼知識點，
而不是題幹裡出現的場景（例如題目用中風病人當情境但其實在考 Berg 量表的計分，主題應是評估與量表）。
confidence 用 0 到 1 表示你有多確定；兩個主題都合理時給 0.5 左右並選較核心的那個。
tags 給 1 到 3 個精確的概念關鍵字（量表名、疾病名、技術名、解剖構造），中文或原文皆可，不要跟主題名稱重複。"""

TOOL_NAME = "tag_questions"


def load_taxonomy():
    return yaml.safe_load(C.TAXONOMY_PATH.read_text("utf-8"))


def subject_topics(tax: dict, subject_code: str) -> list[dict]:
    for s in tax["subjects"]:
        if s["code"] == subject_code:
            return s["topics"]
    raise KeyError(subject_code)


def build_tool(topic_codes: list[str]) -> dict:
    return {
        "name": TOOL_NAME,
        "description": "回傳每一題的主題標記",
        "input_schema": {
            "type": "object",
            "properties": {
                "results": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "id": {"type": "string"},
                            "topic": {"type": "string", "enum": topic_codes},
                            "confidence": {"type": "number", "minimum": 0, "maximum": 1},
                            "tags": {"type": "array", "items": {"type": "string"}, "maxItems": 3},
                        },
                        "required": ["id", "topic", "confidence", "tags"],
                    },
                }
            },
            "required": ["results"],
        },
    }


def format_batch(topics: list[dict], questions: list[dict]) -> str:
    lines = ["## 主題分類表（只能選這些 code）"]
    for t in topics:
        kw = "、".join(t.get("keywords", []))
        grp = f"[{t['group']}] " if t.get("group") else ""
        lines.append(f"- {t['code']}：{grp}{t['name']}（關鍵字：{kw}）")
    lines.append("\n## 題目")
    for q in questions:
        ans = "/".join(q["accepted_answers"]) or ("送分" if q["is_voided"] else "?")
        lines.append(f"\n### {q['id']}\n{q['stem']}")
        for k in C.OPTION_LETTERS:
            lines.append(f"{k}. {q['options'][k]}")
        lines.append(f"答案：{ans}")
    lines.append(f"\n請用 {TOOL_NAME} 工具回傳全部 {len(questions)} 題的結果。")
    return "\n".join(lines)


def call_llm(client, topics: list[dict], questions: list[dict]) -> dict[str, dict]:
    tool = build_tool([t["code"] for t in topics])
    for attempt in range(3):
        try:
            resp = client.messages.create(
                model=C.TAG_MODEL,
                max_tokens=4096,
                system=SYSTEM_PROMPT,
                messages=[{"role": "user", "content": format_batch(topics, questions)}],
                tools=[tool],
                tool_choice={"type": "tool", "name": TOOL_NAME},
            )
            for block in resp.content:
                if block.type == "tool_use" and block.name == TOOL_NAME:
                    return {r["id"]: r for r in block.input["results"]}
            raise RuntimeError("回應裡沒有 tool_use")
        except Exception as e:  # noqa: BLE001
            wait = 5 * (attempt + 1)
            print(f"[warn] API 失敗（{e}），{wait}s 後重試", file=sys.stderr)
            time.sleep(wait)
    raise RuntimeError("連續失敗 3 次")


def iter_files(only: str | None, subject: str | None):
    for p in sorted(C.JSON_DIR.glob("*/*.json")):
        key = f"{p.parent.name}/{p.stem}"
        if only and only != key:
            continue
        if subject and p.stem != subject:
            continue
        yield p, json.loads(p.read_text("utf-8"))


def save(p: Path, data: dict) -> None:
    p.write_text(json.dumps(data, ensure_ascii=False, indent=1), "utf-8")


def tag(args) -> None:
    import anthropic
    client = anthropic.Anthropic()
    tax = load_taxonomy()
    budget = args.limit
    for p, data in iter_files(args.only, args.subject):
        subj = data["exam"]["subject_code"]
        topics = subject_topics(tax, subj)
        todo = [q for q in data["questions"]
                if q.get("topic_source") != "human" and (args.force or not q.get("topic"))]
        if not todo:
            continue
        if budget is not None:
            todo = todo[:budget]
        print(f"{p.parent.name}/{p.stem}: 標記 {len(todo)} 題")
        for i in range(0, len(todo), C.TAG_BATCH_SIZE):
            batch = todo[i:i + C.TAG_BATCH_SIZE]
            if args.dry_run:
                print(format_batch(topics, batch)[:1500] + "\n…")
                return
            results = call_llm(client, topics, batch)
            for q in batch:
                r = results.get(q["id"])
                if not r:
                    print(f"[warn] {q['id']} 沒有回傳結果", file=sys.stderr)
                    continue
                q["topic"] = r["topic"]
                q["topic_confidence"] = round(float(r["confidence"]), 2)
                q["tags"] = [t.strip() for t in r.get("tags", []) if t.strip()][:3]
                q["topic_source"] = "llm"
            save(p, data)  # 每批就存，中斷也不會白做
        if budget is not None:
            budget -= len(todo)
            if budget <= 0:
                break
    print("完成。接著跑 python -m pipeline.validate 看低信心題目。")


def apply_overrides() -> None:
    path = C.REVIEW_DIR / "topic_overrides.csv"
    if not path.exists():
        print(f"沒有 {path}")
        return
    overrides = {}
    with path.open(encoding="utf-8-sig") as f:
        for row in csv.DictReader(f):
            if row.get("id") and row.get("topic"):
                overrides[row["id"].strip()] = row
    n = 0
    for p, data in iter_files(None, None):
        changed = False
        for q in data["questions"]:
            o = overrides.get(q["id"])
            if o:
                q["topic"] = o["topic"].strip()
                tags = [t.strip() for t in (o.get("tags") or "").split("|") if t.strip()]
                if tags:
                    q["tags"] = tags
                q["topic_confidence"] = 1.0
                q["topic_source"] = "human"
                changed = True
                n += 1
        if changed:
            save(p, data)
    print(f"套用 {n} 筆人工覆寫。")


def remap(spec: str) -> None:
    old, new = spec.split("=", 1)
    n = 0
    for p, data in iter_files(None, None):
        changed = False
        for q in data["questions"]:
            if q.get("topic") == old:
                q["topic"] = new
                changed = True
                n += 1
        if changed:
            save(p, data)
    print(f"{old} -> {new}：改了 {n} 題。")


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--subject", help="只標某科，例如 NEURO")
    ap.add_argument("--only", help="只標某檔，例如 108-1/NEURO")
    ap.add_argument("--limit", type=int, help="最多標幾題（試跑用）")
    ap.add_argument("--force", action="store_true", help="已標過的也重標（人工覆寫的除外）")
    ap.add_argument("--dry-run", action="store_true", help="只印出會送給模型的提示")
    ap.add_argument("--apply-overrides", action="store_true")
    ap.add_argument("--remap", help="OLD=NEW")
    args = ap.parse_args(argv)
    if args.apply_overrides:
        apply_overrides()
    elif args.remap:
        remap(args.remap)
    else:
        tag(args)


if __name__ == "__main__":
    main()
