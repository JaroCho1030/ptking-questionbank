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
import logging
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

from . import config as C
from .answers import build_answer_map, extract_text as extract_answer_text
from .textnorm import (clean_text, count_circled, fix_broken_circled, fix_radicals,
                       has_broken_circled, has_cyrillic)

# pdfminer（pdfplumber 底層）遇到字型描述缺欄位會印「Could not get FontBBox…」等提醒，
# 不影響抽字結果，只留真正的錯誤。
logging.getLogger("pdfminer").setLevel(logging.ERROR)

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

def _mupdf_pages(pdf_path: Path):
    try:
        import pymupdf
    except ImportError:  # 舊版只有 fitz 這個名字
        import fitz as pymupdf
    return list(pymupdf.open(str(pdf_path)))


def _broken_glyphs(mpage) -> list[tuple[str, tuple[float, float, float, float], float]]:
    """PyMuPDF 看得到、但 pdfplumber 會丟掉的壞圈號：回傳 (修正後的字, bbox, 字級)。"""
    out = []
    for block in mpage.get_text("rawdict")["blocks"]:
        for line in block.get("lines", []):
            for span in line["spans"]:
                for c in span["chars"]:
                    if has_broken_circled(c["c"]):
                        out.append((fix_broken_circled(c["c"]), tuple(c["bbox"]), span["size"]))
    return out


def glyphs_to_chars(glyphs, template: dict) -> list[dict]:
    """把補回的字做成 pdfplumber 的 char dict（複製同頁一個字當樣板，只改文字與位置）。"""
    chars = []
    for text, (x0, y0, x1, y1), size in glyphs:
        d = dict(template)
        d.update(text=text, x0=x0, x1=x1, top=y0, bottom=y1, width=x1 - x0, height=y1 - y0, size=size,
                 doctop=template["doctop"] - template["top"] + y0)
        chars.append(d)
    return chars


def circled_warnings(final_texts: list[str], mupdf_texts: list[str]) -> list[str]:
    """逐頁比對圈號數量（純函式）。不一致代表某個抽取器漏字，寧可發 warning 也不要默默少字。"""
    warnings = []
    if len(final_texts) != len(mupdf_texts):
        warnings.append(f"兩個抽取器頁數不同（pdfplumber {len(final_texts)}、PyMuPDF {len(mupdf_texts)}）")
    for i, (a, b) in enumerate(zip(final_texts, mupdf_texts), start=1):
        b = fix_broken_circled(b)
        if count_circled(a) != count_circled(b):
            warnings.append(f"第 {i} 頁圈號數量不一致（pdfplumber {count_circled(a)}、PyMuPDF {count_circled(b)}），可能有字被吃掉")
        if has_cyrillic(a):
            warnings.append(f"第 {i} 頁有西里爾字母，可能是新的字型對應錯誤")
    return warnings


def extract_pages(pdf_path: Path, warnings: list[str] | None = None) -> list[list[str]]:
    """每頁一個 list，list 裡是去掉頁首頁尾的文字行。抽取時的警告會附加到 warnings。

    以 pdfplumber 為主，另外兩個補強：
    - dedupe_chars()：部分 PDF 用「疊印」做粗體，同一個字畫好幾次，不去重會變成「殘殘留留」。
    - 圈號字型壞掉的 PDF（例：113-1 NEURO），pdfplumber 會把 ①② 整個丟掉；
      從 PyMuPDF 取回這些字的位置，塞回 pdfplumber 的字元清單再排版。
    """
    import pdfplumber
    from pdfplumber.utils import extract_text as chars_to_text

    mpages = _mupdf_pages(pdf_path)
    texts: list[str] = []
    recovered = 0
    with pdfplumber.open(str(pdf_path)) as pdf:
        for i, page in enumerate(pdf.pages):
            page = page.dedupe_chars()
            glyphs = _broken_glyphs(mpages[i]) if i < len(mpages) else []
            if glyphs and page.chars:
                recovered += len(glyphs)
                text = chars_to_text(list(page.chars) + glyphs_to_chars(glyphs, page.chars[0]))
            else:
                text = page.extract_text()
            texts.append(fix_radicals(text or ""))
    if recovered:
        print(f"  [info] {pdf_path.name}：補回 {recovered} 個圈號（字型對應錯誤）", file=sys.stderr)
    if warnings is not None:
        warnings.extend(circled_warnings(texts, [m.get_text() for m in mpages]))

    pages: list[list[str]] = []
    for text in texts:
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

def figure_boxes(boxes: list[tuple[float, float, float, float]], region: tuple[float, float, float, float],
                 page_area: float) -> list[tuple[float, float, float, float]]:
    """從頁面上所有圖形/圖片框，挑出屬於這一題的（純函式，測試用）。

    - 太小的（線條、雜點）不算：寬 > 20 且高 > 10。
    - 跟頁面差不多大的（例如外框）不算：面積 < 頁面 60%。
    - 至少 60% 面積落在這題的範圍內才算 —— 只「碰到」不夠，否則整頁外框會讓每題都被裁成整頁。
    """
    rx0, ry0, rx1, ry1 = region
    out = []
    for x0, y0, x1, y1 in boxes:
        w, h = x1 - x0, y1 - y0
        if w <= 20 or h <= 10 or w * h >= 0.6 * page_area:
            continue
        ix = max(0.0, min(x1, rx1) - max(x0, rx0))
        iy = max(0.0, min(y1, ry1) - max(y0, ry0))
        if ix * iy >= 0.6 * w * h:
            out.append((x0, y0, x1, y1))
    return out


def crop_figures(pdf_path: Path, questions: list[dict], out_dir: Path, id_for) -> None:
    """找出每題範圍內的圖形/圖片並裁成 WebP。

    題目範圍 = 從本題題號到下一題題號，可以跨頁（圖常被排到下一頁頂端，或選項是照片、分在兩頁）。
    has_figure 以「範圍內真的有圖形物件」為準（表格、照片、線圖），題幹提到「如下圖」只是輔助：
    - 有圖形 → 裁圖；分在多頁就上下拼成一張。
    - 有提示字但找不到圖形 → 不裁圖、標記人工確認。
    重新裁切時會刪掉不再需要的舊圖檔。
    """
    try:
        import pymupdf as fitz
    except ImportError:
        try:
            import fitz  # 舊版 PyMuPDF
        except ImportError:
            print("[warn] 沒有 PyMuPDF，略過附圖裁切（pip install pymupdf）", file=sys.stderr)
            return
    doc = fitz.open(str(pdf_path))
    out_dir.mkdir(parents=True, exist_ok=True)
    by_number = {q["number"]: q for q in questions}
    for q in questions:
        hinted = q["has_figure"]
        q["has_figure"], q["figure"], q["figure_kind"] = False, None, None
        out_path = out_dir / f"{id_for(q['number'])}.webp"
        legacy_png = out_path.with_suffix(".png")  # 2026-10 以前存 PNG
        if legacy_png.exists():
            legacy_png.unlink()
        start = q["page"] - 1
        y0 = _find_question_y(doc[start], q["number"])
        if y0 is None:
            q["review_flags"].append("附圖：找不到題號位置")
            continue
        nxt = by_number.get(q["number"] + 1)
        end = nxt["page"] - 1 if nxt else len(doc) - 1
        y_end = _find_question_y(doc[end], nxt["number"]) if nxt else None

        clips = []  # [(頁碼, 裁切框)]
        for pno in range(start, end + 1):
            page = doc[pno]
            top = y0 - 4 if pno == start else page.rect.y0 + 10
            bottom = (y_end - 2) if (pno == end and y_end is not None) else page.rect.y1 - 36
            if bottom <= top:
                continue
            region = (page.rect.x0 + 24, top, page.rect.x1 - 24, bottom)
            raw = [tuple(d["rect"]) for d in page.get_drawings()] + [tuple(i["bbox"]) for i in page.get_image_info()]
            boxes = figure_boxes(raw, region, page.rect.width * page.rect.height)
            if boxes:
                u = fitz.Rect(boxes[0])
                for bx in boxes[1:]:
                    u |= fitz.Rect(bx)
                clips.append((pno, (u + (-8, -8, 8, 8)) & page.rect))

        if clips:
            _render_clips(fitz, doc, clips, out_path)
            q["has_figure"], q["figure"] = True, f"figures/{out_path.name}"
            q["figure_kind"] = "graphic" if len(clips) == 1 else "graphic_multipage"
            continue
        if out_path.exists():
            out_path.unlink()  # 舊版裁錯的圖
        if hinted:
            q["review_flags"].append("附圖：題幹提到圖表但本題範圍內找不到圖形")
    doc.close()


def _render_clips(fitz, doc, clips, out_path: Path, zoom: float = C.FIGURE_ZOOM,
                  quality: int = C.FIGURE_WEBP_QUALITY) -> None:
    """把一或多個裁切框（可能在不同頁）由上而下拼成一張 WebP。"""
    from PIL import Image
    width = max(c.width for _, c in clips)
    height = sum(c.height for _, c in clips)
    canvas_doc = fitz.open()
    canvas = canvas_doc.new_page(width=width, height=height)
    y = 0.0
    for pno, clip in clips:
        canvas.show_pdf_page(fitz.Rect(0, y, clip.width, y + clip.height), doc, pno, clip=clip)
        y += clip.height
    pix = canvas.get_pixmap(matrix=fitz.Matrix(zoom, zoom), alpha=False)
    Image.frombytes("RGB", (pix.width, pix.height), pix.samples).save(
        str(out_path), "WEBP", quality=quality, method=6)
    canvas_doc.close()


def _find_question_y(page, number: int) -> float | None:
    """題號那一行的 y。先找「N.」後面不接數字的；找不到再放寬（題幹以數字開頭，例如「47.38 歲」）。"""
    strict = re.compile(rf"^\s*{number}[\.．](?!\d)")
    loose = re.compile(rf"^\s*{number}[\.．]")
    fallback = None
    for block in page.get_text("dict")["blocks"]:
        for line in block.get("lines", []):
            text = fix_radicals("".join(span["text"] for span in line.get("spans", [])))
            if strict.match(text):
                return line["bbox"][1]
            if fallback is None and loose.match(text) and line["bbox"][0] < 60:
                fallback = line["bbox"][1]
    return fallback


# ---------------------------------------------------------------- 主流程

def _load_existing_tags(json_path: Path) -> dict[str, dict]:
    if not json_path.exists():
        return {}
    data = json.loads(json_path.read_text("utf-8"))
    return {q["id"]: {k: q.get(k) for k in ("topic", "tags", "topic_confidence", "topic_source")} for q in data.get("questions", [])}


def parse_one(key: str, subj: str, sub: dict, exam_code: str) -> dict:
    roc_year, rnd = (int(x) for x in key.split("-"))
    q_pdf = C.ROOT / sub["q"]["path"]
    extract_warnings: list[str] = []
    pages = extract_pages(q_pdf, extract_warnings)
    header, questions, warnings = parse_pages(pages)
    warnings = extract_warnings + warnings

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
