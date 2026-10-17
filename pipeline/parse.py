"""把考選部試題 PDF 解析成結構化 JSON，並合併答案。

用法：
    python -m pipeline.parse                 # 解析 index.json 裡 needs_parse 或還沒有 JSON 的科目
    python -m pipeline.parse --all           # 全部重新解析（會保留既有的 topic/tags 標記）
    python -m pipeline.parse --only 108-1/NEURO
    python -m pipeline.parse --dump data/raw/108-1/NEURO.Q.pdf   # 印出抽出的文字行，調 regex 用

輸出 data/json/{年}-{次}/{科目}.json，格式見 docs/ARCHITECTURE.md「題庫 JSON」。

版面假設（105 年起的 PDF 皆符合）：
    N.題幹…            題號後面接半形句點，題幹可跨行
    A.選項 / B. / C. / D.   各自一行，可跨行
    題幹提到「如下圖」「下表」「圖中」等 → has_figure，用 PyMuPDF 把該題區域裁成 PNG
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

from . import config as C
from .answers import build_answer_map, extract_text as extract_answer_text
from .textnorm import clean_text, fix_radicals

_Q_START = re.compile(r"^\s*(\d{1,3})[\.．]\s*(.*)$")
_OPT_START = re.compile(r"^\s*([A-DＡ-Ｄ])[\.．]\s*(.*)$")
_PAGE_HDR = re.compile(r"(頁次[:：]|^代\s*號[:：]\s*\d{4}\s*$)")
_PAPER_CODE = re.compile(r"代\s*號[:：]\s*(\d{4})")
_SUBJECT_NAME = re.compile(r"科目名稱[:：]\s*(\S+)")
_CLASS_NAME = re.compile(r"類科名稱[:：]\s*(\S+)")
_FIGURE_HINTS = ("如下圖", "如圖", "下圖", "圖中", "附圖", "如右圖", "見圖", "依圖", "圖示", "此圖",
                 "如下表", "下表", "表中", "附表", "如右表", "見表", "影像判斷", "光影像", "光片", "示意圖")
_FW = {"Ａ": "A", "Ｂ": "B", "Ｃ": "C", "Ｄ": "D"}


# ---------------------------------------------------------------- 文字抽取

def extract_pages(pdf_path: Path) -> list[list[str]]:
    """每頁一個 list，list 裡是去掉頁首頁尾的文字行。"""
    import pdfplumber
    pages: list[list[str]] = []
    with pdfplumber.open(str(pdf_path)) as pdf:
        for page in pdf.pages:
            text = fix_radicals(page.extract_text() or "")
            lines = [ln.rstrip() for ln in text.splitlines()]
            lines = [ln for ln in lines if ln.strip() and not _PAGE_HDR.search(ln)]
            pages.append(lines)
    return pages


# ---------------------------------------------------------------- 結構化（純函式）

def parse_pages(pages: list[list[str]]) -> tuple[dict, list[dict], list[str]]:
    """回傳 (header, questions, warnings)。questions 的欄位見 _new_question。"""
    header_lines: list[str] = []
    questions: list[dict] = []
    warnings: list[str] = []
    cur: dict | None = None
    cur_field: str | None = None  # "stem" 或 "A".."D"
    expected = 1
    started = False

    def flush():
        nonlocal cur
        if cur:
            cur["stem"] = clean_text(cur["stem"])
            for k in C.OPTION_LETTERS:
                cur["options"][k] = clean_text(cur["options"].get(k, ""))
            questions.append(cur)
        cur = None

    for pno, lines in enumerate(pages, start=1):
        for line in lines:
            qm = _Q_START.match(line)
            # 「22.75歲…」是題號 22；「2.5公分」是續行。數字後面接數字時，只有在上一題四個選項都齊了才當新題。
            if qm and int(qm.group(1)) == expected and (
                not qm.group(2)[:1].isdigit() or cur is None or _next_option_letter(cur) is None
            ):
                flush()
                cur = _new_question(expected, qm.group(2), pno)
                cur_field = "stem"
                expected += 1
                started = True
                continue
            if not started:
                header_lines.append(line)
                continue
            om = _OPT_START.match(line)
            if om and cur is not None:
                letter = _FW.get(om.group(1), om.group(1))
                want = _next_option_letter(cur)
                if letter == want:
                    cur["options"][letter] = om.group(2)
                    cur_field = letter
                    continue
                # 不是預期的字母（例如題幹裡的「A.」），當成續行
            if cur is None:
                continue
            if cur_field == "stem":
                cur["stem"] += "\n" + line
            else:
                cur["options"][cur_field] += "\n" + line
    flush()

    header_text = "\n".join(header_lines)
    header = {
        "paper_code": (_PAPER_CODE.search(header_text) or [None, None])[1],
        "subject_name": (_SUBJECT_NAME.search(header_text) or [None, None])[1],
        "class_name": (_CLASS_NAME.search(header_text) or [None, None])[1],
    }
    for q in questions:
        missing = [k for k in C.OPTION_LETTERS if not q["options"].get(k)]
        if missing:
            q["review_flags"].append(f"缺選項 {''.join(missing)}")
        if any(h in q["stem"] for h in _FIGURE_HINTS):
            q["has_figure"] = True
    if len(questions) != C.QUESTIONS_PER_PAPER:
        warnings.append(f"只解析到 {len(questions)} 題（預期 {C.QUESTIONS_PER_PAPER}）")
    return header, questions, warnings


def _new_question(number: int, first_line: str, page: int) -> dict:
    return {
        "number": number, "stem": first_line, "options": {}, "page": page,
        "has_figure": False, "figure": None, "review_flags": [],
    }


def _next_option_letter(q: dict) -> str | None:
    for k in C.OPTION_LETTERS:
        if k not in q["options"]:
            return k
    return None


# ---------------------------------------------------------------- 附圖裁切

def crop_figures(pdf_path: Path, questions: list[dict], out_dir: Path, id_for) -> None:
    """對 has_figure 的題目，用 PyMuPDF 裁出該題區域（優先裁圖形/圖片的聯集框）。"""
    try:
        import fitz  # PyMuPDF
    except ImportError:
        print("[warn] 沒有 PyMuPDF，略過附圖裁切（pip install pymupdf）", file=sys.stderr)
        return
    doc = fitz.open(str(pdf_path))
    out_dir.mkdir(parents=True, exist_ok=True)
    by_number = {q["number"]: q for q in questions}
    for q in questions:
        if not q["has_figure"]:
            continue
        page = doc[q["page"] - 1]
        y0 = _find_question_y(page, q["number"])
        nxt = by_number.get(q["number"] + 1)
        y1 = _find_question_y(page, nxt["number"]) if nxt and nxt["page"] == q["page"] else None
        if y0 is None:
            q["review_flags"].append("附圖：找不到題號位置")
            continue
        if y1 is None:
            y1 = page.rect.y1 - 36
        region = fitz.Rect(page.rect.x0 + 24, y0 - 4, page.rect.x1 - 24, y1 - 2)
        # 圖形/圖片的聯集
        boxes = [fitz.Rect(d["rect"]) for d in page.get_drawings() if fitz.Rect(d["rect"]).intersects(region)]
        boxes += [fitz.Rect(i["bbox"]) for i in page.get_image_info() if fitz.Rect(i["bbox"]).intersects(region)]
        boxes = [b for b in boxes if b.width > 20 and b.height > 10]
        clip = region
        if boxes:
            u = boxes[0]
            for b in boxes[1:]:
                u |= b
            clip = (u + (-8, -8, 8, 8)) & page.rect
            q["figure_kind"] = "graphic"
        else:
            q["figure_kind"] = "region"  # 沒抓到圖形物件，整題區域截圖（可能是表格）
        pix = page.get_pixmap(matrix=fitz.Matrix(2, 2), clip=clip, alpha=False)
        name = f"{id_for(q['number'])}.png"
        pix.save(str(out_dir / name))
        q["figure"] = f"figures/{name}"
    doc.close()


def _find_question_y(page, number: int) -> float | None:
    pat = re.compile(rf"^\s*{number}[\.．](?!\d)")
    for block in page.get_text("dict")["blocks"]:
        for line in block.get("lines", []):
            text = "".join(span["text"] for span in line.get("spans", []))
            if pat.match(fix_radicals(text)):
                return line["bbox"][1]
    return None


# ---------------------------------------------------------------- 主流程

def _load_existing_tags(json_path: Path) -> dict[str, dict]:
    if not json_path.exists():
        return {}
    data = json.loads(json_path.read_text("utf-8"))
    return {q["id"]: {k: q.get(k) for k in ("topic", "tags", "topic_confidence", "topic_source")} for q in data.get("questions", [])}


def parse_one(key: str, subj: str, sub: dict, exam_code: str) -> dict:
    roc_year, rnd = (int(x) for x in key.split("-"))
    q_pdf = C.ROOT / sub["q"]["path"]
    pages = extract_pages(q_pdf)
    header, questions, warnings = parse_pages(pages)

    s_text = extract_answer_text(C.ROOT / sub["s_file"]["path"]) if sub.get("s_file") else ""
    m_text = extract_answer_text(C.ROOT / sub["m_file"]["path"]) if sub.get("m_file") else None
    answer_map, a_warn = build_answer_map(s_text, m_text) if s_text else ({}, ["沒有答案檔"])
    warnings += a_warn

    qid = lambda n: C.question_id(roc_year, rnd, subj, n)  # noqa: E731
    crop_figures(q_pdf, questions, C.FIGURE_DIR, qid)

    out_path = C.JSON_DIR / key / f"{subj}.json"
    keep = _load_existing_tags(out_path)
    return assemble(header, questions, answer_map, warnings, roc_year, rnd, subj, sub, exam_code, keep)


def assemble(header: dict, questions: list[dict], answer_map: dict, warnings: list[str],
             roc_year: int, rnd: int, subj: str, sub: dict, exam_code: str, keep: dict | None = None) -> dict:
    """把解析結果 + 答案組成最終 JSON（純函式，測試用）。"""
    keep = keep or {}
    qid = lambda n: C.question_id(roc_year, rnd, subj, n)  # noqa: E731
    result_questions = []
    for q in questions:
        a = answer_map.get(q["number"])
        if not a:
            q["review_flags"].append("沒有答案")
            a = {"official": None, "accepted": [], "voided": False, "note": None, "needs_review": True}
        if a.get("needs_review"):
            q["review_flags"].append("答案需人工確認")
        tags = keep.get(qid(q["number"]), {})
        result_questions.append({
            "id": qid(q["number"]),
            "number": q["number"],
            "stem": q["stem"],
            "options": {k: q["options"].get(k, "") for k in C.OPTION_LETTERS},
            "official_answer": a["official"],
            "accepted_answers": a["accepted"],
            "is_voided": a["voided"],
            "correction_note": a.get("note"),
            "has_figure": q["has_figure"],
            "figure": q.get("figure"),
            "figure_kind": q.get("figure_kind"),
            "page": q["page"],
            "topic": tags.get("topic"),
            "tags": tags.get("tags") or [],
            "topic_confidence": tags.get("topic_confidence"),
            "topic_source": tags.get("topic_source"),
            "review_flags": q["review_flags"],
        })

    return {
        "exam": {
            "id": C.exam_id(roc_year, rnd, subj),
            "roc_year": roc_year, "round": rnd,
            "subject_code": subj, "subject_name": C.SUBJECT_BY_CODE[subj]["name"],
            "exam_code": exam_code, "class_code": sub.get("class_code"), "subject_param": sub.get("s"),
            "paper_code": header.get("paper_code"),
            "source_q": sub.get("q", {}).get("url"), "source_s": sub.get("s_file", {}).get("url"), "source_m": sub.get("m_file", {}).get("url"),
            "answer_sha256": sub.get("s_file", {}).get("sha256"), "correction_sha256": sub.get("m_file", {}).get("sha256"),
            "parsed_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "warnings": warnings,
        },
        "questions": result_questions,
    }


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--all", action="store_true", help="全部重新解析")
    ap.add_argument("--only", help="只解析某科，例如 108-1/NEURO")
    ap.add_argument("--dump", help="印出某個 Q PDF 抽出的文字行後結束")
    args = ap.parse_args(argv)

    if args.dump:
        for i, lines in enumerate(extract_pages(Path(args.dump)), start=1):
            print(f"=== PAGE {i} ===")
            print("\n".join(lines))
        return

    from .fetch import load_index, save_index
    index = load_index()
    done = 0
    for key, entry in sorted(index["exams"].items()):
        for subj, sub in entry["subjects"].items():
            if not sub.get("q"):
                continue
            out_path = C.JSON_DIR / key / f"{subj}.json"
            if args.only and args.only != f"{key}/{subj}":
                continue
            if not (args.all or args.only or sub.get("needs_parse") or not out_path.exists()):
                continue
            print(f"parse {key}/{subj}")
            data = parse_one(key, subj, sub, entry["exam_code"])
            out_path.parent.mkdir(parents=True, exist_ok=True)
            out_path.write_text(json.dumps(data, ensure_ascii=False, indent=1), "utf-8")
            for w in data["exam"]["warnings"]:
                print(f"  [warn] {w}")
            sub["needs_parse"] = False
            sub["answers_changed"] = False
            done += 1
    save_index(index)
    print(f"完成 {done} 科。接著跑 python -m pipeline.validate")


if __name__ == "__main__":
    main()
