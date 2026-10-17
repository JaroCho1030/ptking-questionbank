"""離線測試：用 108-1 神經科真實 PDF 抽出的文字當 fixture（不需要網路、不需要 PDF 檔）。

跑法：cd questionbank && python -m pytest -q
"""
from __future__ import annotations

import re
import sqlite3
from pathlib import Path

import pytest

from pipeline import config as C
from pipeline.answers import build_answer_map, parse_notes
from pipeline.fetch import parse_exam_options, parse_subject_rows
from pipeline.parse import parse_pages
from pipeline.textnorm import clean_text, fix_radicals

FIX = Path(__file__).parent / "fixtures"


def load_pages(name: str) -> list[list[str]]:
    text = (FIX / name).read_text("utf-8")
    pages = []
    for chunk in re.split(r"^=== PAGE \d+ \(of \d+\) ===\n", text, flags=re.M):
        if chunk.strip():
            pages.append([ln for ln in chunk.splitlines() if ln.strip()])
    return pages


def test_fix_radicals():
    assert fix_radicals("使⽤⼀個⼈") == "使用一個人"
    assert fix_radicals("①②③") == "①②③"  # 不能動圈數字
    assert clean_text("如下圖 所示 的 Berg 量表") == "如下圖所示的 Berg 量表"


def test_parse_question_pdf_text():
    header, qs, warnings = parse_pages(load_pages("108-1-NEURO.Q.txt"))
    assert header["paper_code"] == "4311"
    assert header["subject_name"] == "神經疾病物理治療學"
    assert warnings == []
    assert len(qs) == 80
    assert [q["number"] for q in qs] == list(range(1, 81))
    for q in qs:
        assert all(q["options"][k] for k in C.OPTION_LETTERS), q["number"]
    # 跨頁選項
    assert qs[5]["options"]["B"].startswith("右邊眼睛瞳孔會先縮小")
    # 跨行題幹接合 + 部首修正
    assert "適用於那一個階段" in qs[1]["stem"]
    # 選項內容本身是字母的題目
    assert qs[60]["options"]["A"] == "AAAAARRBBBBBRRAAAAARRBBBBB"
    # 跨頁題幹
    assert qs[56]["stem"].endswith("該病患應屬於那一期？")
    # 附圖偵測
    assert qs[29]["has_figure"] and qs[77]["has_figure"] and qs[51]["has_figure"]
    assert not qs[0]["has_figure"]
    assert qs[79]["page"] == 14


def test_answers_with_correction():
    s = (FIX / "108-1-NEURO.S.txt").read_text("utf-8")
    m = (FIX / "108-1-NEURO.M.txt").read_text("utf-8")
    amap, warnings = build_answer_map(s, m)
    assert warnings == []
    assert len(amap) == 80
    assert amap[1]["accepted"] == ["D"] and amap[80]["accepted"] == ["A"]
    assert amap[27]["accepted"] == ["A", "C"] and amap[27]["official"] == "C"
    assert amap[27]["note"].startswith("第27題")
    assert not amap[27]["needs_review"]


def test_answers_without_correction():
    s = (FIX / "108-1-NEURO.S.txt").read_text("utf-8")
    amap, warnings = build_answer_map(s, None)
    assert warnings == [] and amap[27]["accepted"] == ["C"]


@pytest.mark.parametrize("note,expect", [
    ("第21題答Ａ或Ｃ或AC者均給分", {"accepted": ["A", "C"], "voided": False}),
    ("第5題一律給分。", {"accepted": [], "voided": True}),
    ("第12題答案更正為Ｂ。", {"accepted": ["B"], "voided": False}),
    ("第3題答Ｂ、Ｄ均給分；第9題送分", {"accepted": ["B", "D"], "voided": False}),
])
def test_note_phrasings(note, expect):
    parsed = parse_notes([note])
    num = int(re.search(r"第(\d+)題", note).group(1))
    assert parsed[num]["accepted"] == expect["accepted"]
    assert parsed[num]["voided"] == expect["voided"]


def test_note_multiple_sentences():
    parsed = parse_notes(["第3題答Ｂ、Ｄ均給分；第9題送分"])
    assert parsed[9]["voided"] is True


SEARCH_HTML = """
<select id="ctl00_holderContent_ddlExamCode">
<option value="">所有考試簡稱...</option>
<option value="108100">108年第二次專技人員高等考試醫師考試分階段考試（第一階段）、牙醫師藥師考試分階段考試、醫事檢驗師、醫事放射師、物理治療師、職能治療師</option>
<option value="108030">108年第一次專技人員高等考試醫師牙醫師藥師考試分階段考試、醫事檢驗師、醫事放射師、物理治療師、職能治療師、呼吸治療師、獸醫師考試</option>
<option value="108190">108年特種考試地方政府公務人員考試</option>
</select>
<table class="ULQandA"><tbody>
<tr><td>專技高考_職能治療師</td></tr>
<tr><td><table><tr><td><input id="ctl00_holderContent_chk_108030_312_11" type="checkbox"><label for="ctl00_holderContent_chk_108030_312_11">職能治療學概論</label>
<a href="wHandExamQandA_File.ashx?t=Q&amp;code=108030&amp;c=312&amp;s=11&amp;q=1">試題</a></td></tr></table></td></tr>
<tr><td>專技高考_物理治療師</td></tr>
<tr><td><table><tr>
<td><input id="ctl00_holderContent_chk_108030_311_11" type="checkbox"><label for="ctl00_holderContent_chk_108030_311_11">物理治療基礎學（包括解剖學、生理學、肌動學與生物力學）</label>
<a href="wHandExamQandA_File.ashx?t=Q&amp;code=108030&amp;c=311&amp;s=11&amp;q=1">試題</a><a href="wHandExamQandA_File.ashx?t=S&amp;code=108030&amp;c=311&amp;s=11&amp;q=1">答案</a></td></tr>
<tr><td><input id="ctl00_holderContent_chk_108030_311_44" type="checkbox"><label for="ctl00_holderContent_chk_108030_311_44">神經疾病物理治療學</label>
<a href="wHandExamQandA_File.ashx?t=Q&amp;code=108030&amp;c=311&amp;s=44&amp;q=1">試題</a><a href="wHandExamQandA_File.ashx?t=S&amp;code=108030&amp;c=311&amp;s=44&amp;q=1">答案</a><a href="wHandExamQandA_File.ashx?t=M&amp;code=108030&amp;c=311&amp;s=44&amp;q=1">更正答案</a></td></tr>
</table></td></tr>
</tbody></table>
"""


def test_parse_exam_options():
    found = parse_exam_options(SEARCH_HTML)
    assert [(e["exam_code"], e["roc_year"], e["round"]) for e in found] == [("108100", 108, 2), ("108030", 108, 1)]


def test_parse_subject_rows():
    rows = parse_subject_rows(SEARCH_HTML, "108030")
    assert [r["subject_code"] for r in rows] == ["BASIC", "NEURO"]
    neuro = rows[1]
    assert neuro["class_code"] == "311" and neuro["s"] == "44"
    assert neuro["has_q"] and neuro["has_s"] and neuro["has_m"]
    assert not rows[0]["has_m"]


def test_sqlite_schema_builds(tmp_path):
    from pipeline.build import SCHEMA_SQL
    con = sqlite3.connect(str(tmp_path / "t.sqlite"))
    con.executescript(SCHEMA_SQL)
    con.execute("INSERT INTO questions_fts VALUES ('x','中風偏癱患者的步態','a','b','c','d')")
    # trigram 需要至少 3 個字；App 端 1~2 字的查詢要退回 LIKE
    assert con.execute("SELECT id FROM questions_fts WHERE questions_fts MATCH '偏癱患'").fetchone()[0] == "x"
    assert con.execute("SELECT id FROM questions_fts WHERE questions_fts MATCH '偏癱'").fetchone() is None


def test_assemble_validate_build_end_to_end(tmp_path, monkeypatch):
    """fixture → JSON → validate → build sqlite，不碰網路與 PDF。"""
    import json
    from pipeline import build as B, validate as V
    from pipeline.parse import assemble

    header, qs, warnings = parse_pages(load_pages("108-1-NEURO.Q.txt"))
    s = (FIX / "108-1-NEURO.S.txt").read_text("utf-8")
    m = (FIX / "108-1-NEURO.M.txt").read_text("utf-8")
    amap, _ = build_answer_map(s, m)
    data = assemble(header, qs, amap, warnings, 108, 1, "NEURO", {"class_code": "311", "s": "44"}, "108030")
    assert data["exam"]["id"] == "108-1-NEURO" and data["exam"]["paper_code"] == "4311"
    q27 = data["questions"][26]
    assert q27["id"] == "108-1-NEURO-027" and q27["accepted_answers"] == ["A", "C"] and q27["official_answer"] == "C"

    json_dir, dist_dir, review_dir = tmp_path / "json", tmp_path / "dist", tmp_path / "review"
    (json_dir / "108-1").mkdir(parents=True)
    (json_dir / "108-1" / "NEURO.json").write_text(json.dumps(data, ensure_ascii=False), "utf-8")
    monkeypatch.setattr(C, "JSON_DIR", json_dir)
    monkeypatch.setattr(C, "DIST_DIR", dist_dir)
    monkeypatch.setattr(C, "REVIEW_DIR", review_dir)
    monkeypatch.setattr(C, "DATA_DIR", tmp_path)

    V.main([])
    rows = (review_dir / "review_queue.csv").read_text("utf-8-sig").splitlines()
    # 三題疑似附圖但沒裁到圖 → 進審核
    assert len(rows) == 1 + 3

    B.build("2026.10.16-1", "https://example.com/dl")
    con = sqlite3.connect(str(dist_dir / "questions.sqlite"))
    assert con.execute("SELECT COUNT(*) FROM questions").fetchone()[0] == 80
    assert con.execute("SELECT accepted_answers FROM questions WHERE id='108-1-NEURO-027'").fetchone()[0] == "A,C"
    assert con.execute("SELECT COUNT(*) FROM topics").fetchone()[0] > 60
    assert con.execute("SELECT id FROM questions_fts WHERE questions_fts MATCH '肌梭'").fetchone() is None  # 2 字
    assert con.execute("SELECT id FROM questions_fts WHERE questions_fts MATCH '布朗斯壯'").fetchone()[0].startswith("108-1-NEURO")
    manifest = json.loads((dist_dir / "manifest.json").read_text("utf-8"))
    assert manifest["question_count"] == 80 and manifest["sqlite"]["url"].endswith("/questions.sqlite")
