"""解析答案 PDF (t=S) 與更正答案 PDF (t=M) 的文字。

兩種檔的版面相同：
    題 數： 80題
    題號            （M 檔是「題序」）
    答案 Ｄ Ｂ Ｃ ... （每列 20 個，全形字母；M 檔裡被更正的題目用 ＃）
    備 註：
    第27題答Ａ、Ｃ給分。      ← 只有 M 檔有，而且措辭不固定

輸出 answer_map: {題號: {"official": "D", "accepted": ["A","C"], "voided": False, "note": str|None,
                        "needs_review": bool}}
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

from .textnorm import fix_radicals, normalize_answer_letter

_COUNT_RE = re.compile(r"題\s*數[:：]\s*(\d+)\s*題")
_ANSWER_LINE_RE = re.compile(r"^\s*答\s*案\s*[:：]?\s*(.*)$")
_TOKEN_RE = re.compile(r"[ＡＢＣＤＥABCDEabcde＃#]")
_NOTE_SPLIT_RE = re.compile(r"(?=第\s*\d+\s*題)")
_NOTE_Q_RE = re.compile(r"第\s*(\d+)\s*題")
_LETTERS_RE = re.compile(r"[ＡＢＣＤＥABCDE]")
_VOID_RE = re.compile(r"(一律給分|均給分|皆給分|送分|全部給分|所有作答者均給分|不予計分|均予給分|都給分)")


def extract_text(pdf_path: Path) -> str:
    import pdfplumber
    parts = []
    with pdfplumber.open(str(pdf_path)) as pdf:
        for page in pdf.pages:
            parts.append(page.extract_text() or "")
    return fix_radicals("\n".join(parts))


def parse_answer_text(text: str) -> tuple[list[str], int | None, list[str]]:
    """回傳 (tokens, expected_count, note_lines)。tokens 是依序出現的 A-E 或 '#'。"""
    expected = None
    m = _COUNT_RE.search(text)
    if m:
        expected = int(m.group(1))
    tokens: list[str] = []
    notes: list[str] = []
    in_notes = False
    for raw in text.splitlines():
        line = raw.strip()
        if not line:
            continue
        if re.match(r"^備\s*註", line):
            in_notes = True
            rest = re.sub(r"^備\s*註\s*[:：]?", "", line).strip()
            if rest:
                notes.append(rest)
            continue
        am = _ANSWER_LINE_RE.match(line)
        if am and not in_notes:
            for tok in _TOKEN_RE.findall(am.group(1)):
                tokens.append("#" if tok in "＃#" else normalize_answer_letter(tok))
            continue
        if in_notes:
            # 備註區會夾著「題序 / 答案 / 01 02 03 …」的版面殘留，過濾掉
            if re.match(r"^(題\s*[號序]|答\s*案)\s*$", line) or re.match(r"^[\d\s]+$", line):
                continue
            notes.append(line)
    return tokens, expected, notes


def parse_notes(notes: list[str]) -> dict[int, dict]:
    """把備註句子解析成 {題號: {"accepted": [...], "voided": bool, "note": str, "needs_review": bool}}。"""
    joined = " ".join(notes)
    out: dict[int, dict] = {}
    for chunk in _NOTE_SPLIT_RE.split(joined):
        chunk = chunk.strip(" 。.;；")
        if not chunk:
            continue
        qm = _NOTE_Q_RE.search(chunk)
        if not qm:
            continue
        num = int(qm.group(1))
        body = chunk[qm.end():]
        entry = {"accepted": [], "voided": False, "note": chunk, "needs_review": False}
        if _VOID_RE.search(body) and not _LETTERS_RE.search(body):
            entry["voided"] = True
        else:
            letters = []
            for tok in _LETTERS_RE.findall(body):
                L = normalize_answer_letter(tok)
                if L not in letters:
                    letters.append(L)
            if letters:
                entry["accepted"] = letters
            else:
                entry["needs_review"] = True
        out[num] = entry
    return out


def build_answer_map(s_text: str, m_text: str | None, expected_count: int = 80) -> tuple[dict[int, dict], list[str]]:
    """合併 S 與 M。回傳 (answer_map, warnings)。"""
    warnings: list[str] = []
    s_tokens, s_expected, _ = parse_answer_text(s_text)
    n = s_expected or expected_count
    if len(s_tokens) != n:
        warnings.append(f"S 檔答案數 {len(s_tokens)} != 題數 {n}")
    answer_map: dict[int, dict] = {}
    for i, tok in enumerate(s_tokens, start=1):
        if tok == "#":
            warnings.append(f"S 檔第 {i} 題出現 #（不預期）")
            answer_map[i] = {"official": None, "accepted": [], "voided": False, "note": None, "needs_review": True}
        else:
            answer_map[i] = {"official": tok, "accepted": [tok], "voided": False, "note": None, "needs_review": False}

    if m_text:
        m_tokens, m_expected, m_notes = parse_answer_text(m_text)
        if len(m_tokens) != n:
            warnings.append(f"M 檔答案數 {len(m_tokens)} != 題數 {n}")
        notes = parse_notes(m_notes)
        for i, tok in enumerate(m_tokens, start=1):
            entry = answer_map.setdefault(i, {"official": None, "accepted": [], "voided": False, "note": None, "needs_review": True})
            if tok == "#":
                info = notes.get(i)
                if not info:
                    warnings.append(f"M 檔第 {i} 題標 # 但備註找不到說明")
                    entry["needs_review"] = True
                    continue
                entry["note"] = info["note"]
                if info["voided"]:
                    entry["voided"] = True
                    entry["accepted"] = []
                elif info["accepted"]:
                    entry["accepted"] = info["accepted"]
                else:
                    entry["needs_review"] = True
                entry["source"] = "M"
            else:
                if entry.get("official") and entry["official"] != tok:
                    # M 檔的非 # 題答案與 S 檔不同：以 M 為準但要人看
                    warnings.append(f"第 {i} 題 S={entry['official']} 但 M={tok}，以 M 為準")
                    entry["accepted"] = [tok]
                    entry["needs_review"] = True
                    entry["source"] = "M"
        # 備註提到但沒標 # 的題目（偶爾發生）
        for num, info in notes.items():
            if num <= n and answer_map.get(num, {}).get("note") is None and (info["accepted"] or info["voided"]):
                e = answer_map[num]
                e["note"] = info["note"]
                if info["voided"]:
                    e["voided"], e["accepted"] = True, []
                else:
                    e["accepted"] = info["accepted"]
                e["source"] = "M"
                warnings.append(f"第 {num} 題備註有更正但答案列未標 #，已套用")
    return answer_map, warnings


if __name__ == "__main__":  # 快速檢查：python -m pipeline.answers data/raw/108-1/NEURO.S.pdf [M.pdf]
    s = extract_text(Path(sys.argv[1]))
    m = extract_text(Path(sys.argv[2])) if len(sys.argv) > 2 else None
    amap, warns = build_answer_map(s, m)
    for k, v in amap.items():
        if v.get("note") or v["needs_review"]:
            print(k, v)
    print("count", len(amap), "warnings", warns)
