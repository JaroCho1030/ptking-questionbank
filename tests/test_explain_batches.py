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
