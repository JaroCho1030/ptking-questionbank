"""檢查 data/json 的完整性，產出人工審核清單 data/review/review_queue.csv。

用法：
    python -m pipeline.validate            # 檢查全部
    python -m pipeline.validate --strict   # 有任何錯誤就以非 0 結束（CI 用）

檢查項目：
    - 每科 80 題、題號連續、四個選項都有文字、題幹不空
    - 答案：accepted_answers 非空或 is_voided；字母只在 A-D
    - 同一科內重複題幹（通常代表解析把兩題黏在一起）
    - 題目的 review_flags（缺選項、答案需確認、附圖問題）
    - 主題：topic 必須在 taxonomy 內；topic_confidence 低於門檻列入審核；未標記的統計
"""
from __future__ import annotations

import argparse
import csv
import json
import sys
from collections import Counter
from pathlib import Path

import yaml

from . import config as C


def load_taxonomy() -> dict:
    tax = yaml.safe_load(C.TAXONOMY_PATH.read_text("utf-8"))
    topics = {}
    for s in tax["subjects"]:
        for t in s["topics"]:
            topics[t["code"]] = {"subject": s["code"], "name": t["name"], "group": t.get("group")}
    return {"raw": tax, "topics": topics}


def iter_json_files():
    for p in sorted(C.JSON_DIR.glob("*/*.json")):
        yield p, json.loads(p.read_text("utf-8"))


def validate_file(path: Path, data: dict, topics: dict) -> tuple[list[dict], list[str]]:
    """回傳 (review_rows, errors)。review_rows 是要人看的；errors 是結構性錯誤。"""
    rows, errors = [], []
    exam = data["exam"]
    qs = data["questions"]
    label = exam["id"]

    if len(qs) != C.QUESTIONS_PER_PAPER:
        errors.append(f"{label}: 題數 {len(qs)} != {C.QUESTIONS_PER_PAPER}")
    nums = [q["number"] for q in qs]
    if nums != list(range(1, len(qs) + 1)):
        errors.append(f"{label}: 題號不連續")
    stems = Counter(q["stem"] for q in qs)
    for stem, n in stems.items():
        if n > 1:
            errors.append(f"{label}: 重複題幹 x{n}: {stem[:30]}…")

    for q in qs:
        reasons = list(q.get("review_flags") or [])
        if not q["stem"].strip():
            reasons.append("題幹空白")
        for k in C.OPTION_LETTERS:
            if not (q["options"].get(k) or "").strip():
                reasons.append(f"選項 {k} 空白")
        if not q["is_voided"] and not q["accepted_answers"]:
            reasons.append("沒有答案")
        bad = [a for a in q["accepted_answers"] if a not in C.OPTION_LETTERS]
        if bad:
            reasons.append(f"答案字母異常 {bad}")
        if q["has_figure"] and not q.get("figure"):
            reasons.append("疑似附圖但沒有裁到圖")
        if q.get("topic"):
            if q["topic"] not in topics:
                reasons.append(f"topic 不在分類表：{q['topic']}")
            elif topics[q["topic"]]["subject"] != exam["subject_code"]:
                reasons.append(f"topic 屬於別科：{q['topic']}")
            elif (q.get("topic_confidence") or 0) < C.TAG_LOW_CONFIDENCE and q.get("topic_source") == "llm":
                reasons.append(f"主題信心低 {q['topic_confidence']:.2f}")
        if reasons:
            rows.append({
                "id": q["id"], "reasons": "；".join(dict.fromkeys(reasons)),
                "stem": q["stem"][:60], "answer": "/".join(q["accepted_answers"]) or ("送分" if q["is_voided"] else ""),
                "topic": q.get("topic") or "", "note": q.get("correction_note") or "",
                "source_q": exam.get("source_q") or "",
            })
    return rows, errors


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--strict", action="store_true")
    args = ap.parse_args(argv)

    tax = load_taxonomy()
    all_rows, all_errors = [], []
    total, tagged, voided, figures = 0, 0, 0, 0
    for path, data in iter_json_files():
        rows, errors = validate_file(path, data, tax["topics"])
        all_rows += rows
        all_errors += errors
        for q in data["questions"]:
            total += 1
            tagged += bool(q.get("topic"))
            voided += bool(q["is_voided"])
            figures += bool(q.get("figure"))

    C.REVIEW_DIR.mkdir(parents=True, exist_ok=True)
    out = C.REVIEW_DIR / "review_queue.csv"
    with out.open("w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=["id", "reasons", "stem", "answer", "topic", "note", "source_q"])
        w.writeheader()
        w.writerows(all_rows)

    print(f"題目總數 {total}，已標主題 {tagged}，送分 {voided}，附圖 {figures}")
    print(f"待審核 {len(all_rows)} 題 → {out}")
    for e in all_errors:
        print("[error]", e)
    if args.strict and all_errors:
        sys.exit(1)


if __name__ == "__main__":
    main()
