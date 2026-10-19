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
    assert fix_radicals("⻑柄梳、⺠國、⻄醫") == "長柄梳、民國、西醫"  # 部首補充區，NFKC 不處理
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


def test_clean_text_latin_apostrophe():
    assert clean_text("克隆氏病（Crohn’s Disease）") == "克隆氏病（Crohn's Disease）"
    assert clean_text("Colles’ fracture") == "Colles' fracture"
    assert clean_text("「中文’」") == "「中文’」"


def test_tag_prompt_includes_group_context():
    from pipeline.tag import format_batch
    lead = {"id": "x-1", "stem": "王先生58歲，左腳突然無力", "options": {k: "a" for k in "ABCD"},
            "accepted_answers": ["A"], "is_voided": False, "group_id": "x-1", "group_pos": 1}
    follow = dict(lead, id="x-2", stem="承上題，最可能的診斷？", group_pos=2, figure="figures/x-2.webp")
    text = format_batch([{"code": "T-1", "name": "腦中風"}], [follow], {"x-1": lead, "x-2": follow})
    assert "題組前題 x-1" in text and "王先生58歲" in text and "附圖" in text


# ---- 複數主題（ARCHITECTURE 3.8）----

def _neuro_data():
    from pipeline.parse import assemble
    header, qs, warnings = parse_pages(load_pages("108-1-NEURO.Q.txt"))
    amap, _ = build_answer_map((FIX / "108-1-NEURO.S.txt").read_text("utf-8"), (FIX / "108-1-NEURO.M.txt").read_text("utf-8"))
    return assemble(header, qs, amap, warnings, 108, 1, "NEURO", {"class_code": "311", "s": "44"}, "108030")


def _setup_dirs(tmp_path, monkeypatch, data):
    import json
    json_dir, dist_dir, review_dir = tmp_path / "json", tmp_path / "dist", tmp_path / "review"
    (json_dir / "108-1").mkdir(parents=True)
    (json_dir / "108-1" / "NEURO.json").write_text(json.dumps(data, ensure_ascii=False), "utf-8")
    monkeypatch.setattr(C, "JSON_DIR", json_dir)
    monkeypatch.setattr(C, "DIST_DIR", dist_dir)
    monkeypatch.setattr(C, "REVIEW_DIR", review_dir)
    monkeypatch.setattr(C, "DATA_DIR", tmp_path)
    return dist_dir, review_dir


def test_clean_secondary_topics():
    from pipeline.tag import clean_secondary
    valid = {"NEURO-STROKE", "NEURO-ASSESS", "BASIC-KIN-GAIT", "BASIC-ANA-NEU"}
    r = {"secondary_topics": ["NEURO-STROKE", "BASIC-KIN-GAIT", "BASIC-KIN-GAIT", "NOPE", "BASIC-ANA-NEU"]}
    # 與主要相同、重複、不在分類表的都去掉；最多 2 個；可跨科
    assert clean_secondary(r, "NEURO-STROKE", valid) == ["BASIC-KIN-GAIT", "BASIC-ANA-NEU"]
    assert clean_secondary({}, "NEURO-STROKE", valid) == []


def test_tag_prompt_and_tool_allow_cross_subject_secondary():
    from pipeline import tag as T
    tax = T.load_taxonomy()
    topics = T.subject_topics(tax, "NEURO")
    other = T.other_subject_topics(tax, "NEURO")
    assert other and all(not t["code"].startswith("NEURO-") for t in other)
    tool = T.build_tool([t["code"] for t in topics], T.all_topic_codes(tax))
    props = tool["input_schema"]["properties"]["results"]["items"]["properties"]
    assert "NEURO-BALANCE" in props["topic"]["enum"]
    assert "BASIC-KIN-GAIT" in props["topic"]["enum"]              # 本科只有籠統主題時主要主題可跨科（3.8）
    assert "BASIC-KIN-GAIT" in props["secondary_topics"]["items"]["enum"]  # 次要主題不限科
    assert props["secondary_topics"]["maxItems"] == 2
    assert "secondary_topics" not in tool["input_schema"]["properties"]["results"]["items"]["required"]
    q = {"id": "x-1", "stem": "題幹", "options": {k: "o" for k in C.OPTION_LETTERS},
         "accepted_answers": ["A"], "is_voided": False}
    text = T.format_batch(topics, [q], {"x-1": q}, other)
    assert "BASIC-KIN-GAIT" in text and "本科只有籠統主題時也可當主要主題" in text
    assert "本科只有籠統或勉強相關的主題、而別科有專門的主題時選別科" in T.SYSTEM_PROMPT and "不另掛本科次要主題" in T.SYSTEM_PROMPT


def test_tag_prompt_secondary_rules():
    # ARCHITECTURE 3.8（2026-10-18 問答）：低信心不拿次要主題當備案；看考點、情境不算；純支配加解剖
    from pipeline.tag import SYSTEM_PROMPT
    assert "不要用次要主題當備案" in SYSTEM_PROMPT and "alt_topic" in SYSTEM_PROMPT and "0.3：拿不準" in SYSTEM_PROMPT
    assert "信心低於 0.6 且另有合理候選時才加" not in SYSTEM_PROMPT
    assert "情境不算" in SYSTEM_PROMPT and "神經支配範圍" in SYSTEM_PROMPT


def test_validate_secondary_topics(tmp_path, monkeypatch):
    from pipeline import validate as V
    data = _neuro_data()
    qs = data["questions"]
    qs[0].update(topic="NEURO-STROKE", secondary_topics=["BASIC-KIN-GAIT"])               # 合法（跨科）
    qs[1].update(topic="NEURO-STROKE", secondary_topics=["NEURO-NOPE"])                    # 不在分類表
    qs[2].update(topic="NEURO-STROKE", secondary_topics=["NEURO-STROKE"])                  # 與主要相同
    qs[3].update(topic="NEURO-STROKE", secondary_topics=["BASIC-KIN-GAIT", "BASIC-KIN-GAIT"])  # 重複
    qs[4].update(topic=None, secondary_topics=["BASIC-KIN-GAIT"])                          # 沒有主要
    _, review_dir = _setup_dirs(tmp_path, monkeypatch, data)
    V.main([])
    text = (review_dir / "review_queue.csv").read_text("utf-8-sig")
    assert text.count("次要主題不在分類表") == 1
    assert text.count("次要主題與主要主題相同") == 1
    assert text.count("次要主題重複") == 1
    assert text.count("有次要主題但沒有主要主題") == 1
    import csv, io
    reasons = {r["id"]: r["reasons"] for r in csv.DictReader(io.StringIO(text))}
    assert "次要主題" not in reasons.get(qs[0]["id"], "")  # 合法的跨科次要主題不會被擋


def test_build_question_topics(tmp_path, monkeypatch):
    from pipeline import build as B
    data = _neuro_data()
    qs = data["questions"]
    qs[0].update(topic="NEURO-STROKE", secondary_topics=["BASIC-KIN-GAIT", "BASIC-ANA-NEU", "NEURO-FOUND"])  # 第 3 個超過上限
    qs[1].update(topic="NEURO-FOUND", secondary_topics=["NEURO-FOUND", "NOPE"])  # 與主要相同、不存在 → 都不進表
    qs[2].update(topic="NEURO-ASSESS", secondary_topics=[])
    dist_dir, _ = _setup_dirs(tmp_path, monkeypatch, data)
    B.build("2026.10.18-1", "https://example.com/dl")
    con = sqlite3.connect(str(dist_dir / "questions.sqlite"))
    rows = lambda qid: con.execute("SELECT topic_code, rank FROM question_topics WHERE question_id=? ORDER BY rank", (qid,)).fetchall()
    assert rows(qs[0]["id"]) == [("NEURO-STROKE", 0), ("BASIC-KIN-GAIT", 1), ("BASIC-ANA-NEU", 2)]
    assert rows(qs[1]["id"]) == [("NEURO-FOUND", 0)]
    assert rows(qs[2]["id"]) == [("NEURO-ASSESS", 0)]
    # 篩選用：某主題（含次要）會找到哪些題
    ids = {r[0] for r in con.execute("SELECT question_id FROM question_topics WHERE topic_code='BASIC-KIN-GAIT'")}
    assert ids == {qs[0]["id"]}
    # 主要主題欄位維持不變
    assert con.execute("SELECT topic_code FROM questions WHERE id=?", (qs[0]["id"],)).fetchone()[0] == "NEURO-STROKE"


def test_overrides_secondary_column(tmp_path, monkeypatch):
    import json
    from pipeline import tag as T
    data = _neuro_data()
    data["questions"][0].update(topic="NEURO-STROKE", secondary_topics=["BASIC-KIN-GAIT"], topic_source="llm")
    data["questions"][1].update(topic="NEURO-STROKE", secondary_topics=["BASIC-KIN-GAIT"], topic_source="llm")
    data["questions"][2].update(topic="NEURO-STROKE", secondary_topics=["BASIC-KIN-GAIT"], topic_source="llm")
    _, review_dir = _setup_dirs(tmp_path, monkeypatch, data)
    review_dir.mkdir(parents=True, exist_ok=True)
    a, b, c = (data["questions"][i]["id"] for i in range(3))
    (review_dir / "topic_overrides.csv").write_text(
        "id,topic,tags,secondary\n"
        f"{a},NEURO-FOUND,,BASIC-ANA-NEU|NEURO-FOUND\n"   # 換次要；與主要相同的會被去掉
        f"{b},NEURO-FOUND,,-\n"                            # 清空次要
        f"{c},NEURO-FOUND,,\n", "utf-8")                   # 留空 = 不動
    T.apply_overrides()
    out = json.loads((C.JSON_DIR / "108-1" / "NEURO.json").read_text("utf-8"))["questions"]
    assert out[0]["secondary_topics"] == ["BASIC-ANA-NEU"] and out[0]["topic_source"] == "human"
    assert out[1]["secondary_topics"] == []
    assert out[2]["secondary_topics"] == ["BASIC-KIN-GAIT"]


def test_remap_updates_secondary(tmp_path, monkeypatch):
    import json
    from pipeline import tag as T
    data = _neuro_data()
    data["questions"][0].update(topic="NEURO-STROKE", secondary_topics=["BASIC-KIN-GAIT"])
    _setup_dirs(tmp_path, monkeypatch, data)
    T.remap("BASIC-KIN-GAIT=BASIC-KIN-GAIT2")
    out = json.loads((C.JSON_DIR / "108-1" / "NEURO.json").read_text("utf-8"))["questions"]
    assert out[0]["secondary_topics"] == ["BASIC-KIN-GAIT2"]


# ---- 子代理分批（ARCHITECTURE 6，2026-10-19）----

def test_export_then_import_results(tmp_path, monkeypatch):
    import json
    from types import SimpleNamespace
    from pipeline import tag as T
    data = _neuro_data()
    data["questions"][1].update(topic="NEURO-FOUND", topic_source="human")   # 人工覆寫的不匯出、不覆蓋
    _setup_dirs(tmp_path, monkeypatch, data)
    batches, results = tmp_path / "batches", tmp_path / "results"
    T.export_batches(SimpleNamespace(only="108-1/NEURO", subject=None, limit=4, force=True), batches)
    manifest = json.loads((batches / "manifest.json").read_text("utf-8"))
    ids = manifest["108-1_NEURO"]["ids"]
    assert len(ids) == 4 and data["questions"][1]["id"] not in ids
    md = (batches / "108-1_NEURO.md").read_text("utf-8")
    assert "不要用次要主題當備案" in md and '"batch": "108-1_NEURO"' in md and ids[0] in md

    results.mkdir()
    rows = [
        {"id": ids[0], "topic": "NEURO-STROKE", "confidence": 0.9, "tags": ["a", " ", "b"], "secondary_topics": ["BASIC-KIN-GAIT", "NEURO-STROKE"],
         "alt_topic": "NEURO-ASSESS"},
        {"id": ids[1], "topic": "INTRO-SAFETY", "confidence": 0.8, "tags": []},     # 跨科主要主題 → 可以寫入
        {"id": ids[2], "topic": "NEURO-FOUND", "confidence": 1.7, "tags": [], "alt_topic": "NOPE"},  # 信心夾到 1；alt 不在分類表 → 丟掉
        {"id": data["questions"][1]["id"], "topic": "NEURO-STROKE", "confidence": 0.9, "tags": []},  # 人工題 → 不動
    ]                                                                                 # ids[3] 缺 → 列出
    (results / "108-1_NEURO.json").write_text(json.dumps({"batch": "108-1_NEURO", "results": rows}), "utf-8")
    T.import_results(results, batches)
    out = {q["id"]: q for q in json.loads((C.JSON_DIR / "108-1" / "NEURO.json").read_text("utf-8"))["questions"]}
    assert out[ids[0]]["topic"] == "NEURO-STROKE" and out[ids[0]]["secondary_topics"] == ["BASIC-KIN-GAIT"]
    assert out[ids[0]]["tags"] == ["a", "b"] and out[ids[0]]["topic_source"] == "llm"
    assert out[ids[1]]["topic"] == "INTRO-SAFETY" and out[ids[1]]["topic_source"] == "llm"
    assert out[ids[2]]["topic_confidence"] == 1.0 and out[ids[2]]["alt_topic"] is None
    assert out[ids[0]]["alt_topic"] == "NEURO-ASSESS"
    assert out[data["questions"][1]["id"]]["topic"] == "NEURO-FOUND"
    assert out[ids[3]].get("topic_source") != "llm"


def test_parse_keeps_secondary_topics(tmp_path):
    import json
    from pipeline.parse import _load_existing_tags
    p = tmp_path / "x.json"
    p.write_text(json.dumps({"questions": [{"id": "q", "topic": "NEURO-FOUND", "secondary_topics": ["BASIC-KIN-GAIT"]}]}), "utf-8")
    assert _load_existing_tags(p)["q"]["secondary_topics"] == ["BASIC-KIN-GAIT"]


def test_taxonomy_topics_have_definitions():
    # 每個主題都要有 definition；excludes 裡提到的 code 必須存在、且不能指回自己
    from pipeline.taxonomy_doc import CODE_RE, load
    tax = load()
    topics = [t for s in tax["subjects"] for t in s["topics"]]
    codes = {t["code"] for t in topics}
    assert len(codes) == len(topics) == 100
    for t in topics:
        assert t.get("definition"), t["code"]
        assert "歸" not in t["name"], t["code"]          # 歸屬說明放 excludes，不放 App 顯示的名稱
        for e in t.get("excludes") or []:
            assert "→" in e, (t["code"], e)
            for c in CODE_RE.findall(e):
                assert c in codes and c != t["code"], (t["code"], c)


def test_taxonomy_doc_in_sync():
    # docs/TAXONOMY.md 由 taxonomy.yaml 產生；改了 yaml 要重跑 python -m pipeline.taxonomy_doc
    from pipeline.taxonomy_doc import DOC_PATH, load, render
    if not DOC_PATH.exists():
        # docs/ 不在 questionbank repo 裡（GitHub Actions 的 checkout 沒有），只在本機檢查
        pytest.skip("docs/TAXONOMY.md 不在這個 checkout")
    text = render(load())
    assert DOC_PATH.read_text("utf-8") == text
    assert "NEURO-ASSESS" in text and "**不含**" in text and "**關鍵字**" in text


def test_format_batch_includes_definitions():
    from pipeline import tag as T
    tax = T.load_taxonomy()
    topics = T.subject_topics(tax, "NEURO")
    q = {"id": "x-1", "stem": "題幹", "options": {k: "o" for k in C.OPTION_LETTERS},
         "accepted_answers": ["A"], "is_voided": False}
    text = T.format_batch(topics, [q], {"x-1": q}, T.other_subject_topics(tax, "NEURO"))
    assert "包含：" in text and "不含：Brunnstrom 分期 → NEURO-ASSESS" in text and "關鍵字：" in text
    assert "不含" in T.SYSTEM_PROMPT


def test_trim_clip_keeps_option_text_out():
    """105-1-NEURO-030：圖下緣留白裁進選項 A 那一行 → 收回到那行之上；圖旁標籤不動。"""
    from pipeline.parse import trim_clip
    fig = (60, 100, 400, 300)
    clip = (52, 92, 408, 308)
    stem_line = (40, 88, 500, 98)       # 上方題幹最後一行
    option_a = (40, 304, 300, 316)      # 下方選項 A
    label = (380, 200, 420, 212)        # 圖旁標籤，跟圖垂直重疊
    far = (40, 400, 300, 412)           # 沒碰到
    assert trim_clip(fig, clip, [stem_line, option_a, label, far]) == (52, 99, 408, 303)


def test_trim_clip_tolerance():
    from pipeline.parse import trim_clip
    fig = (60, 100, 400, 300)
    # 文字行緊貼圖（間距 < gap）→ 收到那行之上 gap 處
    assert trim_clip(fig, (52, 92, 408, 308), [(40, 300.5, 300, 312)]) == (52, 92, 408, 299.5)
    # 圖片框壓進選項行 1.8pt（106-2-CARDPED-019）→ 收進圖，切掉那行
    assert trim_clip(fig, (52, 92, 408, 308), [(40, 298.2, 300, 317)]) == (52, 92, 408, 297.2)
    # 壓進超過 tol 的行當成圖的一部分，不動
    assert trim_clip(fig, (52, 92, 408, 308), [(40, 290, 300, 310)]) == (52, 92, 408, 308)


def test_line_image_and_known_formula_glyph():
    from pipeline.parse import is_inline_glyph, is_line_image
    assert is_line_image((0, 0, 45, 15.7))          # V̇E/V̇CO₂：比行內字寬
    assert not is_inline_glyph((0, 0, 45, 15.7))
    assert not is_line_image((0, 0, 186, 15.4))     # 被切成條的大圖
    assert not is_line_image((0, 0, 3, 17.7))       # 細線
    assert not is_line_image((0, 0, 59, 26.3))      # 選項是小圖（FDA 標章）


def test_drop_reviewed():
    from pipeline.validate import drop_reviewed
    rows = [{"id": "a", "reasons": "選項 A 空白"}, {"id": "b", "reasons": "附圖：x"}, {"id": "a2", "reasons": "y"}]
    reviewed = {("a", "選項 A 空白"), ("b", "附圖：舊原因")}
    kept, n = drop_reviewed(rows, reviewed)
    assert [r["id"] for r in kept] == ["b", "a2"] and n == 1   # 原因變了要再出現


def test_include_option_labels():
    """107-2-TECH-076：選項是照片，「A.」在圖左邊 35.8–47，圖從 47 開始 → 裁切框要往左包到代號。"""
    from pipeline.parse import include_option_labels
    fig = (47, 275, 88, 523)
    clip = (39, 267, 96, 531)
    lines = [((35.8, 275.9, 47.0, 292.4), "A."), ((35.8, 338.1, 47.0, 354.6), "B."),
             ((35.8, 540.9, 120.8, 560.1), "A.髖關節屈肌攣縮"),   # 圖下方的別題選項：不動
             ((300, 280, 400, 292), "如圖所示")]                  # 不是選項代號
    assert include_option_labels(fig, clip, lines) == (34.8, 267, 96, 531)


def test_include_bare_label_below_figure():
    """114-2-ORTHO-028：「B.」印在第一頁底、B 圖在下一頁 → 框進第一頁的裁切；有文字的選項行不框。"""
    from pipeline.parse import include_option_labels
    fig = (58, 603, 145, 740)
    clip = (50, 595, 153, 748)
    assert include_option_labels(fig, clip, [((45.0, 749.8, 57.9, 762.4), "B.")]) == (44.0, 595, 153, 763.4)
    assert include_option_labels(fig, clip, [((45.0, 749.8, 300, 762.4), "B.雙側非對稱性")]) == clip


def test_include_labels_when_options_interleave_with_images():
    """115-2-ORTHO-033：「A.文字」圖「B.文字」圖…交錯 → 最上面的 A、最下面的 C 也要框進來。"""
    from pipeline.parse import include_option_labels
    fig = (50, 323, 275, 686)
    clip = (42, 322, 283, 694)    # trim_clip 收過的
    a = ((39.2, 309.8, 204.1, 320.8), "A.以背部支撐方式之腳踏車訓練")
    b = ((39.2, 546.8, 198.6, 557.8), "B.以上坡坡度模式的跑步機訓練")
    c = ((39.2, 699.8, 220.7, 710.8), "C.以搭配四肢動作之越野滑雪機訓練")
    assert include_option_labels(fig, clip, [a, b, c]) == (38.2, 308.8, 283, 711.8)
    # 沒有交錯（圖是題幹的一部分）→ 圖下方的選項 A 不框
    assert include_option_labels(fig, clip, [c]) == clip


def test_apply_rejudge():
    from pipeline.tag import apply_rejudge
    q = {"topic": "INTRO-MED", "alt_topic": "NEURO-VESTIB", "secondary_topics": ["NEURO-VESTIB", "BASIC-ANA-NEU"],
         "topic_confidence": 0.5, "topic_source": "llm"}
    assert apply_rejudge(q, "INTRO-MED", "NEURO-VESTIB", {"choice": "B", "confidence": 0.8}) is None
    assert (q["topic"], q["alt_topic"], q["secondary_topics"], q["topic_confidence"]) == \
        ("NEURO-VESTIB", "INTRO-MED", ["BASIC-ANA-NEU"], 0.8)
    assert apply_rejudge(q, "INTRO-MED", "NEURO-VESTIB", {"choice": "A", "confidence": 0.8})  # 主題已變 → 略過
    h = {"topic": "X", "alt_topic": "Y", "topic_source": "human"}
    assert apply_rejudge(h, "X", "Y", {"choice": "B", "confidence": 0.9}) and h["topic"] == "X"


def test_rejudge_prompt_has_rule():
    from pipeline.tag import REJUDGE_PROMPT
    assert "本科只有籠統或勉強相關的主題" in REJUDGE_PROMPT and "NEURO-VESTIB" in REJUDGE_PROMPT
