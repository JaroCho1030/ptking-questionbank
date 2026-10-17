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
    ("第63題除未作答者不給分外，其餘均給分", {"accepted": ["A", "B", "C", "D"], "voided": False}),
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


def test_user_agent_is_latin1():
    """HTTP header 必須能用 latin-1 編碼，否則 requests 送出前就會 UnicodeEncodeError。"""
    from pipeline import config as C
    C.USER_AGENT.encode("latin-1")


def test_fix_broken_circled():
    from pipeline.textnorm import fix_broken_circled, has_broken_circled
    raw = "\u045b總時間 \u045c相對時間 \u0460環境因素"  # 113-1 NEURO 用 PyMuPDF 抽出的樣子
    assert has_broken_circled(raw)
    assert fix_broken_circled(raw) == "①總時間 ②相對時間 ⑥環境因素"
    assert not has_broken_circled("①②③ 正常的圈號")


def test_glyphs_to_chars_places_recovered_glyph():
    from pipeline.parse import glyphs_to_chars
    tmpl = {"text": "總", "x0": 50, "x1": 61, "top": 100, "bottom": 111, "doctop": 900, "size": 11, "upright": True}
    [c] = glyphs_to_chars([("①", (39, 100, 50, 111), 11)], tmpl)
    assert (c["text"], c["x0"], c["doctop"], c["upright"]) == ("①", 39, 900, True)


def test_circled_warnings():
    from pipeline.parse import circled_warnings
    assert circled_warnings(["A.①②"], ["A.\u045b\u045c"]) == []
    assert any("圈號數量不一致" in w for w in circled_warnings(["A.②"], ["A.①②"]))
    assert any("西里爾" in w for w in circled_warnings(["A.\u045b"], ["A.\u045b"]))


def test_figure_boxes_ignores_page_frame_and_neighbours():
    from pipeline.parse import figure_boxes
    page_area = 595 * 842
    region = (24, 300, 571, 500)
    frame = (20, 20, 575, 822)        # 整頁外框：不算
    own = (60, 330, 400, 480)         # 本題的圖：算
    neighbour = (60, 480, 400, 700)   # 下一題的圖，只碰到邊：不算
    line = (60, 350, 500, 351)        # 細線：不算
    assert figure_boxes([frame, own, neighbour, line], region, page_area) == [own]


def test_render_clips_writes_webp(tmp_path):
    import pymupdf
    from pipeline.parse import _render_clips
    doc = pymupdf.open()
    for _ in range(2):  # 兩頁各畫一個框，模擬跨頁附圖
        page = doc.new_page(width=300, height=400)
        page.draw_rect(pymupdf.Rect(50, 50, 250, 150), color=(0, 0, 0), fill=(0.8, 0.2, 0.2))
    out = tmp_path / "x.webp"
    _render_clips(pymupdf, doc, [(0, pymupdf.Rect(40, 40, 260, 160)), (1, pymupdf.Rect(40, 40, 260, 160))], out)
    data = out.read_bytes()
    assert data[:4] == b"RIFF" and data[8:12] == b"WEBP"
    from PIL import Image
    assert Image.open(out).size == (440, 480)  # 220x120pt ×2 倍、兩張上下拼


def test_parse_exam_options_combined_title():
    """105 年的考試名稱把物理、職能、呼吸治療師合寫，不能只比對「物理治療」。"""
    html = """<select id="ctl00_holderContent_ddlExamCode">
      <option value="105100">105年第二次醫師牙醫師藥師分階段、藥師醫檢師醫放師助產師物理職能呼吸治療師獸醫師考試</option>
      <option value="105090">105年第二次中醫師分階段營養師心理師護理師社工師暨法醫師語言治療師聽力師牙體技術師考試</option>
      <option value="105020">105年第一次醫師牙醫師藥師分階段、藥師醫檢師醫放師助產師物理職能呼吸治療師獸醫師考試</option>
      <option value="105150">105年公務人員高等考試一級暨二級考試</option>
    </select>"""
    found = parse_exam_options(html)
    assert [(e["exam_code"], e["roc_year"], e["round"]) for e in found] == [("105100", 105, 2), ("105020", 105, 1)]



def test_merge_strips_rebuilds_sliced_image():
    """105-1 TECH 第 3 題：一張圖被切成很多 1pt 高的圖片條，要先合併才不會被當雜點濾掉。"""
    from pipeline.parse import figure_boxes, merge_strips
    strips = [(50 + (i % 3), 300 + i, 150 - (i % 3), 301 + i) for i in range(80)]
    glyph = (218, 504, 226, 513)  # 隔很遠的行內圖片字：不併入
    merged = merge_strips(strips + [glyph])
    assert (50, 300, 150, 380) in merged and glyph in merged and len(merged) == 2
    assert figure_boxes(merged, (24, 290, 571, 450), 595 * 842) == [(50, 300, 150, 380)]


def test_has_figure_hint_ignores_scale_names():
    from pipeline.parse import has_figure_hint
    assert not has_figure_hint("下列量表中，何者最適合評估多發性硬化症？")
    assert has_figure_hint("承上題，有關表中的「90%信賴區間」")


def test_inline_glyph_size():
    from pipeline.parse import is_inline_glyph
    assert is_inline_glyph((218, 504, 232, 518))       # 14×14 的「拇」
    assert not is_inline_glyph((50, 300, 150, 380))    # 附圖
    assert not is_inline_glyph((60, 350, 70, 351))     # 細線


def _ch(text, x0, top, size):
    return {"text": text, "x0": x0, "x1": x0 + size * 0.6, "top": top, "bottom": top + size,
            "doctop": top, "size": size, "upright": True, "fontname": "F", "width": size * 0.6, "height": size}


def test_normalize_scripts_subscript_and_superscript():
    from pipeline.parse import normalize_scripts
    from pdfplumber.utils import extract_text
    # cmH₂O：下標 2 字小、往下偏
    h2o = [_ch("H", 67.7, 160.3, 8.5), _ch("2", 72.8, 164.7, 7.1), _ch("O", 77.1, 160.3, 8.5)]
    assert extract_text(normalize_scripts(h2o)) == "H₂O"
    # Ca²⁺：上標往上偏
    ca = [_ch("C", 10, 100, 10), _ch("a", 16, 100, 10), _ch("2", 22, 97, 7), _ch("+", 26.2, 97, 7)]
    assert extract_text(normalize_scripts(ca)) == "Ca²⁺"
    # 同樣大小、同一行的字不動
    plain = [_ch("A", 10, 100, 10), _ch("2", 16, 100, 10)]
    assert extract_text(normalize_scripts(plain)) == "A2"


def test_normalize_scripts_keeps_unmappable_run_plain():
    from pipeline.parse import normalize_scripts
    from pdfplumber.utils import extract_text
    # 下標「bc」沒有 Unicode 下標 b → 整段保持原字，但要回到同一行
    chars = [_ch("X", 10, 100, 10), _ch("b", 16, 105, 7), _ch("c", 20.2, 105, 7), _ch("Y", 25, 100, 10)]
    assert extract_text(normalize_scripts(chars)) == "XbcY"


def test_normalize_scripts_ignores_smaller_option_text():
    """109-2 CARDPED：「A.」12.8pt、選項文字 10.8pt 且偏低，不是下標。"""
    from pipeline.parse import normalize_scripts
    from pdfplumber.utils import extract_text
    chars = [_ch("A", 45.4, 646.4, 12.8), _ch(".", 53.1, 646.4, 12.8)]
    x = 60.8
    for t in "pH=7.25，":
        chars.append(_ch(t, x, 651.6, 10.8)); x += 6.48
    assert normalize_scripts(chars) == chars


def test_normalize_scripts_chained_sub_sup():
    from pipeline.parse import normalize_scripts
    from pdfplumber.utils import extract_text
    chars = [_ch("C", 10, 100, 10), _ch("O", 16, 100, 10), _ch("3", 22, 105, 7), _ch("-", 26.2, 97, 7)]
    assert extract_text(normalize_scripts(chars)) == "CO₃⁻"


def test_assign_groups():
    from pipeline.parse import assign_groups
    stems = {
        1: "一般題目", 2: "王先生，58歲…", 3: "承上題，最可能的診斷？", 4: "承上題，下一步？",
        5: "請依下列迴歸分析結果表回答以下3題。何者影響社會參與？", 6: "年齡越大時分數如何？", 7: "由此分析結果發現…",
        8: "承上題之家庭所得統計圖，何者正確？", 9: "一般題目",
    }
    qs = [{"number": n, "stem": s} for n, s in stems.items()]
    assign_groups(qs)
    got = {q["number"]: (q["group_lead"], q["group_pos"]) for q in qs}
    assert got[1] == (None, None) and got[9] == (None, None)
    assert [got[n] for n in (2, 3, 4)] == [(2, 1), (2, 2), (2, 3)]
    assert [got[n] for n in (5, 6, 7, 8)] == [(5, 1), (5, 2), (5, 3), (5, 4)]


def test_note_trailing_comma_stripped():
    assert parse_notes(["第22題答Ａ、Ｂ給分，"])[22]["note"] == "第22題答Ａ、Ｂ給分"


def test_clean_text_strips_control_and_pua():
    assert clean_text("何者正確？\x81①水平") == "何者正確？①水平"
    assert clean_text("伸趾長肌") == "伸拇趾長肌"


def test_normalize_scripts_half_size_subscript():
    """111-2 BASIC：下標 2 只有一半大、底線沒低於基準字，靠「頂端明顯偏低」判定。"""
    from pipeline.parse import normalize_scripts
    from pdfplumber.utils import extract_text
    chars = [_ch("C", 171.0, 444.8, 11.0), _ch("O", 177.9, 444.8, 11.0), _ch("2", 185.4, 449.2, 5.5)]
    chars[1]["x1"] = 185.4
    assert extract_text(normalize_scripts(chars)) == "CO₂"
