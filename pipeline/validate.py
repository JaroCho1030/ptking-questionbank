"""檢查 data/json 的完整性，產出人工審核清單 data/review/review_queue.csv。

用法：
    python -m pipeline.validate            # 檢查全部
    python -m pipeline.validate --strict   # 有任何錯誤就以非 0 結束（CI 用）

檢查項目：
    - 每科 80 題、題號連續、四個選項都有文字、題幹不空
    - 答案：accepted_answers 非空或 is_voided；字母只在 A-D
    - 同一科內重複題幹（通常代表解析把兩題黏在一起）
    - 題目的 review_flags（缺選項、答案需確認、附圖問題）
    - 主題：topic 必須在 taxonomy 內（可跨科）；topic_confidence 低於門檻列入審核；未標記的統計
      次要主題 secondary_topics：最多 2 個、必須在 taxonomy 內（可跨科）、不可與主要主題相同或重複
    - 題幹／選項殘留部首區段的字（textnorm 沒對照到的 ⻑ 之類）
    - AI 詳解（data/explanations）：status 合法、題目存在；status = pass 的欄位齊、字數上限、why_others 只含存在且非正解的選項
      （錯了算結構錯誤）；通過的 answers_hash 與現況不符（答案被更正）→ 審核清單，原因 explanation_stale

已審紀錄 data/review/reviewed.csv（id,reasons,reviewed_at,note）：同一題、同樣原因審過就不再列入；
原因變了（例如重新 parse 後多了新問題）會再出現。
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
from .explain import answers_hash, format_problems, iter_explanation_files, STATUSES
from .textnorm import _is_radical

REVIEWED_FIELDS = ["id", "reasons", "reviewed_at", "note"]


def load_reviewed(path: Path | None = None) -> set[tuple[str, str]]:
    path = path or C.REVIEW_DIR / "reviewed.csv"
    if not path.exists():
        return set()
    with path.open(encoding="utf-8-sig") as f:
        return {(r["id"].strip(), r["reasons"].strip()) for r in csv.DictReader(f) if r.get("id")}


def drop_reviewed(rows: list[dict], reviewed: set[tuple[str, str]]) -> tuple[list[dict], int]:
    """拿掉同 id、同原因已審過的列（純函式，測試用）。"""
    kept = [r for r in rows if (r["id"], r["reasons"]) not in reviewed]
    return kept, len(rows) - len(kept)


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


def check_explanation(q: dict, e: dict) -> tuple[list[str], list[str]]:
    """一則詳解 → (審核原因, 結構錯誤)。"""
    reasons, errors = [], []
    if e.get("status") not in STATUSES:
        errors.append(f"{q['id']}: 詳解 status 不合法 {e.get('status')!r}")
    if e.get("status") == "pass":
        errors += [f"{q['id']}: 詳解{p}" for p in format_problems(q, e)]
        # 通過的詳解答案被更正：build 不收錄，explain 下次會重寫（人工寫的要人工改）
        if e.get("answers_hash") != answers_hash(q):
            reasons.append("explanation_stale")
    return reasons, errors


def validate_file(path: Path, data: dict, topics: dict,
                  explanations: dict[str, dict] | None = None) -> tuple[list[dict], list[str]]:
    """回傳 (review_rows, errors)。review_rows 是要人看的；errors 是結構性錯誤。
    explanations：這份試卷的詳解 {id: entry}（data/explanations 同名檔）。"""
    rows, errors = [], []
    explanations = explanations or {}
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

    groups: dict[str, list[dict]] = {}
    for q in qs:
        if q.get("group_id"):
            groups.setdefault(q["group_id"], []).append(q)
    for gid, members in groups.items():
        nums = [m["number"] for m in members]
        if (len(members) < 2 or members[0]["id"] != gid or [m["group_pos"] for m in members] != list(range(1, len(members) + 1))
                or nums != list(range(nums[0], nums[0] + len(nums)))):
            errors.append(f"{label}: 題組 {gid} 結構不對（題號 {nums}）")

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
        text = q["stem"] + "".join(q["options"].values())
        leftovers = sorted({ch for ch in text if _is_radical(ch)})
        if leftovers:
            reasons.append(f"部首字殘留 {''.join(leftovers)}")
        if q["has_figure"] and not q.get("figure"):
            reasons.append("疑似附圖但沒有裁到圖")
        if q.get("topic"):
            if q["topic"] not in topics:
                reasons.append(f"topic 不在分類表：{q['topic']}")
            elif (q.get("topic_confidence") or 0) < C.TAG_LOW_CONFIDENCE and q.get("topic_source") == "llm":
                reasons.append(f"主題信心低 {q['topic_confidence']:.2f}")
        sec = q.get("secondary_topics") or []
        if sec and not q.get("topic"):
            reasons.append("有次要主題但沒有主要主題")
        if len(sec) > 2:
            reasons.append(f"次要主題超過 2 個：{sec}")
        if len(set(sec)) != len(sec):
            reasons.append(f"次要主題重複：{sec}")
        for c in sec:
            if c not in topics:
                reasons.append(f"次要主題不在分類表：{c}")
            elif c == q.get("topic"):
                reasons.append(f"次要主題與主要主題相同：{c}")
        if q["id"] in explanations:
            r, e = check_explanation(q, explanations[q["id"]])
            reasons += r
            errors += e
        if reasons:
            rows.append({
                "id": q["id"], "reasons": "；".join(dict.fromkeys(reasons)),
                "stem": q["stem"][:60], "answer": "/".join(q["accepted_answers"]) or ("送分" if q["is_voided"] else ""),
                "topic": q.get("topic") or "", "alt_topic": q.get("alt_topic") or "", "note": q.get("correction_note") or "",
                "source_q": exam.get("source_q") or "",
            })
    return rows, errors


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--strict", action="store_true")
    args = ap.parse_args(argv)

    tax = load_taxonomy()
    all_rows, all_errors = [], []
    total, tagged, voided, figures, grouped, group_ids = 0, 0, 0, 0, 0, set()
    expl = {(d, s): entries for d, s, entries in iter_explanation_files()}
    seen_papers = set()
    for path, data in iter_json_files():
        key = (path.parent.name, path.stem)
        seen_papers.add(key)
        entries = expl.get(key, {})
        known = {q["id"] for q in data["questions"]}
        all_errors += [f"{qid}: 詳解對應的題目不存在" for qid in sorted(set(entries) - known)]
        rows, errors = validate_file(path, data, tax["topics"], entries)
        all_rows += rows
        all_errors += errors
        for q in data["questions"]:
            total += 1
            tagged += bool(q.get("topic"))
            voided += bool(q["is_voided"])
            figures += bool(q.get("figure"))
            if q.get("group_id"):
                grouped += 1
                group_ids.add(q["group_id"])

    for d, s in sorted(set(expl) - seen_papers):
        all_errors.append(f"data/explanations/{d}/{s}.json: 沒有對應的試卷")
    n_pass = sum(e.get("status") == "pass" for entries in expl.values() for e in entries.values())
    n_expl = sum(len(entries) for entries in expl.values())

    all_rows, n_reviewed = drop_reviewed(all_rows, load_reviewed())
    C.REVIEW_DIR.mkdir(parents=True, exist_ok=True)
    out = C.REVIEW_DIR / "review_queue.csv"
    with out.open("w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=["id", "reasons", "stem", "answer", "topic", "alt_topic", "note", "source_q"])
        w.writeheader()
        w.writerows(all_rows)

    print(f"題目總數 {total}，已標主題 {tagged}，送分 {voided}，附圖 {figures}，題組 {len(group_ids)} 組 {grouped} 題，詳解 {n_expl} 則（通過 {n_pass}）")
    print(f"待審核 {len(all_rows)} 題 → {out}（已審略過 {n_reviewed} 題）")
    for e in all_errors:
        print("[error]", e)
    if args.strict and all_errors:
        sys.exit(1)


if __name__ == "__main__":
    main()
