"""子代理分批模式（pipeline.explain_batches）的離線測試。"""
from __future__ import annotations

import json

from pipeline import config as C
from pipeline import explain as X
from pipeline import explain_batches as B
from tests.test_explain import _q, _write_paper, dirs  # noqa: F401（fixture）


def _paper(dirs, n=3):
    qs = [_q(f"110-1-NEURO-{i:03d}", accepted=("C",)) for i in range(1, n + 1)]
    qs[1]["accepted_answers"] = []
    qs[1]["is_voided"] = True            # 送分題
    _write_paper(dirs, "110-1", "NEURO", qs)
    return qs


def _write_result(out, role, name, rows):
    p = B.result_path(out, role, name)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps({"batch": name, "results": rows}, ensure_ascii=False), "utf-8")


def test_export_writes_three_kinds_of_batches(dirs, capsys):
    qs = _paper(dirs, n=3)
    out = dirs / "batches"
    B.export(out, X.select_todo(years=[110]), batch_size=2)
    manifest = B.load_manifest(out)
    assert list(manifest["batches"]) == ["110-1_NEURO_1", "110-1_NEURO_2"]
    assert manifest["batches"]["110-1_NEURO_1"]["ids"] == [qs[0]["id"], qs[1]["id"]]
    assert manifest["hashes"][qs[0]["id"]] == X.answers_hash(qs[0])
    writer = (out / "writer" / "110-1_NEURO_1.md").read_text("utf-8")
    blind = (out / "blind" / "110-1_NEURO_1.md").read_text("utf-8")
    assert "承認答案：C" in writer and "一律給分" in writer and "results/writer/110-1_NEURO_1.json" in writer
    assert "承認答案" not in blind and "給分" not in blind and "results/blind/110-1_NEURO_1.json" in blind
    assert "工具回傳" not in writer and "工具回傳" not in blind        # 子代理寫檔，不用工具
    assert "不要去讀題庫 JSON" in blind
    assert not (out / "check").exists()


def test_export_check_skips_format_failures(dirs, capsys):
    qs = _paper(dirs, n=2)
    out = dirs / "batches"
    B.export(out, X.select_todo(years=[110]), batch_size=10)
    name = "110-1_NEURO_1"
    _write_result(out, "writer", name, [
        {"id": qs[0]["id"], "why_correct": "因為如此。", "why_others": {"A": "不對", "B": "不對", "D": "不對"}, "takeaway": "記住。"},
        {"id": qs[1]["id"], "why_correct": "考選部公告一律給分。", "why_others": {}, "takeaway": "記" * 200},   # takeaway 太長
    ])
    _write_result(out, "blind", name, [{"id": qs[0]["id"], "answer": ["C"], "confidence": 0.9},
                                       {"id": qs[1]["id"], "answer": ["A"], "confidence": 0.3}])
    B.export_check(out)
    check = (out / "check" / f"{name}.md").read_text("utf-8")
    assert qs[0]["id"] in check and qs[1]["id"] not in check
    assert "你先前沒看答案時的作答：C" in check and "待審的詳解" in check
    assert "直接不通過 1 題" in capsys.readouterr().out


def test_import_judges_like_explain(dirs, capsys):
    qs = _paper(dirs, n=3)
    out = dirs / "batches"
    B.export(out, X.select_todo(years=[110]), batch_size=10)
    name = "110-1_NEURO_1"
    good = {"why_correct": "因為如此。", "why_others": {"A": "不對", "B": "不對", "D": "不對"}, "takeaway": "記住。"}
    _write_result(out, "writer", name, [
        {"id": qs[0]["id"], **good},
        {"id": qs[1]["id"], "why_correct": "考選部公告一律給分。", "why_others": {}, "takeaway": "記住。"},
        {"id": qs[2]["id"], **good},
    ])
    _write_result(out, "blind", name, [{"id": qs[0]["id"], "answer": ["C"], "confidence": 0.9},
                                       {"id": qs[1]["id"], "answer": ["A"], "confidence": 0.3},
                                       {"id": qs[2]["id"], "answer": ["B"], "confidence": 0.8}])   # 盲解答錯
    _write_result(out, "check", name, [{"id": qs[0]["id"], "verdict": "pass", "reason": "都對"},
                                       {"id": qs[1]["id"], "verdict": "pass", "reason": "爭議說明合理"},
                                       {"id": qs[2]["id"], "verdict": "reasoning_error", "reason": "B 的說明錯"}])
    written = B.import_results(out, "subagent test")
    assert written[qs[0]["id"]]["status"] == "pass" and written[qs[0]["id"]]["model"] == "subagent test"
    assert written[qs[1]["id"]]["status"] == "pass"                       # 送分題不比盲解答案
    assert written[qs[2]["id"]]["status"] == "rejected"
    assert written[qs[2]["id"]]["review"]["fails"] == ["blind_mismatch", "reasoning_error"]
    saved = X.load_explanations("110-1", "NEURO")
    assert saved[qs[0]["id"]]["why_others"] == {"A": "不對", "B": "不對", "D": "不對"}
    out_text = capsys.readouterr().out
    assert "通過 2、不通過 1" in out_text


def test_import_reports_missing_and_bad_rows(dirs, capsys):
    qs = _paper(dirs, n=2)
    out = dirs / "batches"
    B.export(out, X.select_todo(years=[110]), batch_size=10)
    name = "110-1_NEURO_1"
    _write_result(out, "writer", name, [
        {"id": qs[0]["id"], "why_correct": "因為如此。", "why_others": {"A": "不對", "B": "不對", "D": "不對"}, "takeaway": "記住。"},
        {"id": "999-9-NEURO-001", "why_correct": "x", "why_others": {}, "takeaway": "x"},
    ])
    _write_result(out, "blind", name, [{"id": qs[0]["id"], "answer": ["Z"], "confidence": 0.9}])   # 答案異常
    written = B.import_results(out, "m")
    # 第一題：寫手有、盲解異常 → pending；第二題寫手缺 → 不寫
    assert written[qs[0]["id"]]["status"] == "pending"
    assert qs[1]["id"] not in written
    err = capsys.readouterr().err
    assert "多出不在批次裡的 id" in err and "盲解答案異常" in err and "缺這題" in err


def test_iter_explanation_files_skips_batch_state(dirs):
    qs = _paper(dirs, n=2)
    X.save_explanations("110-1", "NEURO", {qs[0]["id"]: {"id": qs[0]["id"], "status": "pass"}})
    state = dirs / "explanations" / ".batches"
    state.mkdir(parents=True)
    (state / "run-20261103T000000Z.json").write_text("{}", "utf-8")
    assert [(d, s) for d, s, _ in X.iter_explanation_files()] == [("110-1", "NEURO")]


def test_release_notes_mention_new_explanations():
    from pipeline import release_notes as R
    prev = {"bank_version": "2026.10.19-1", "question_count": 10560, "exams": [{"id": "115-2-NEURO"}]}
    cur = {"bank_version": "2026.11.03-1", "question_count": 10560, "explanation_count": 5197,
           "exams": [{"id": "115-2-NEURO", "roc_year": 115, "round": 2, "question_count": 80}]}
    lines = R._changes(cur, prev)
    assert lines[0].startswith("新增 AI 詳解：5,197 題") and "收錄的考試不變" not in "".join(lines)
    more = R._changes({**cur, "explanation_count": 5300}, {**prev, "explanation_count": 5197})
    assert more[0] == "AI 詳解增加 103 題，共 5,300 題。"
    same = R._changes({**cur, "explanation_count": 5197}, {**prev, "explanation_count": 5197})
    assert same[0] == "收錄的考試不變，修正題目內容、答案或主題分類。"


def test_select_retry_and_export_retry(dirs, capsys):
    from tests.test_explain import _entry
    qs = [_q(f"110-1-NEURO-{i:03d}", accepted=("C",)) for i in range(1, 7)]
    _write_paper(dirs, "110-1", "NEURO", qs)
    bad = {"why_correct": "因為如此。", "why_others": {"A": "不對", "B": "不對", "D": "不對"}, "takeaway": "記" * 61}
    X.save_explanations("110-1", "NEURO", {
        qs[0]["id"]: _entry(qs[0]),                                                                   # 通過 → 不收
        qs[1]["id"]: _entry(qs[1], status="rejected", review={"verdict": "reasoning_error", "reason": "B 的說明錯",
                                                             "fails": ["reasoning_error"]}),       # 收
        qs[2]["id"]: _entry(qs[2], status="rejected", review={"verdict": "pass", "fails": ["blind_mismatch"]}),  # 盲解錯 → 不收
        qs[3]["id"]: _entry(qs[3], status="rejected", review={"fails": ["format"]}, **bad),           # 格式 → 收
        qs[4]["id"]: _entry(qs[4], status="pending"),                                                 # 收
        qs[5]["id"]: _entry(qs[5], status="rejected", source="human",
                            review={"verdict": "reasoning_error", "fails": ["reasoning_error"]}),    # 人工 → 不收
    })
    rows = X.select_retry()
    assert [r[2]["id"][-3:] for r in rows] == ["002", "004", "005"]
    assert rows[0][3]["review"]["reason"] == "B 的說明錯"
    out = dirs / "retry"
    B.export(out, [r[:3] for r in rows], batch_size=2, previous={r[2]["id"]: r[3] for r in rows})
    manifest = B.load_manifest(out)
    assert list(manifest["batches"]) == ["retry_1", "retry_2"]
    assert manifest["batches"]["retry_1"]["ids"] == [qs[1]["id"], qs[3]["id"]]
    assert manifest["previous"][qs[1]["id"]]["reason"] == "B 的說明錯"
    writer = (out / "writer" / "retry_1.md").read_text("utf-8")
    assert "上一版詳解" in writer and "審稿意見：B 的說明錯" in writer and "takeaway 61 字 > 60" in writer
    blind = (out / "blind" / "retry_1.md").read_text("utf-8")
    assert "上一版詳解" not in blind and "承認答案" not in blind


def test_import_retry_keeps_previous_review_when_rejected_again(dirs, capsys):
    from tests.test_explain import _entry
    qs = [_q(f"110-1-NEURO-{i:03d}", accepted=("C",)) for i in range(1, 3)]
    _write_paper(dirs, "110-1", "NEURO", qs)
    X.save_explanations("110-1", "NEURO", {
        q["id"]: _entry(q, status="rejected", review={"verdict": "reasoning_error", "reason": "舊的理由", "fails": ["reasoning_error"]})
        for q in qs})
    rows = X.select_retry()
    out = dirs / "retry"
    B.export(out, [r[:3] for r in rows], batch_size=10, previous={r[2]["id"]: r[3] for r in rows})
    good = {"why_correct": "改好了。", "why_others": {"A": "不對", "B": "不對", "D": "不對"}, "takeaway": "記住。"}
    _write_result(out, "writer", "retry_1", [{"id": q["id"], **good} for q in qs])
    _write_result(out, "blind", "retry_1", [{"id": q["id"], "answer": ["C"], "confidence": 0.9} for q in qs])
    _write_result(out, "check", "retry_1", [{"id": qs[0]["id"], "verdict": "pass", "reason": "都對"},
                                            {"id": qs[1]["id"], "verdict": "reasoning_error", "reason": "新的理由"}])
    written = B.import_results(out, "subagent retry")
    saved = X.load_explanations("110-1", "NEURO")
    assert saved[qs[0]["id"]]["status"] == "pass" and "previous" not in saved[qs[0]["id"]]["review"]
    assert saved[qs[1]["id"]]["status"] == "rejected"
    assert saved[qs[1]["id"]]["review"]["reason"] == "新的理由"
    assert saved[qs[1]["id"]]["review"]["previous"] == {"verdict": "reasoning_error", "reason": "舊的理由", "fails": ["reasoning_error"]}
    assert not X.select_retry() or [r[2]["id"] for r in X.select_retry()] == [qs[1]["id"]]


def test_export_lists_group_figure_png(dirs):
    from PIL import Image
    (dirs / "figures").mkdir()
    Image.new("RGB", (4, 4)).save(dirs / "figures" / "110-1-NEURO-001.webp", "WEBP")
    first = _q("110-1-NEURO-001", group_id="110-1-NEURO-001", group_pos=1, stem="下圖……回答下列 2 題。",
               figure="figures/110-1-NEURO-001.webp", has_figure=True)
    second = _q("110-1-NEURO-002", group_id="110-1-NEURO-001", group_pos=2, stem="承上題？")
    _write_paper(dirs, "110-1", "NEURO", [first, second])
    out = dirs / "batches"
    B.export(out, X.select_todo(), batch_size=10)
    blind = (out / "blind" / "110-1_NEURO_1.md").read_text("utf-8")
    png = (out / "figures" / "110-1-NEURO-001.png").resolve()
    assert png.exists()
    assert blind.count(str(png)) == 2                       # 第一題「本題附圖」、第二題「同組第 1 題的附圖」各一次
    assert "同組第 1 題的附圖：" in blind and "本題附圖：" in blind
