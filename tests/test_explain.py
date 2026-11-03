"""AI 詳解（pipeline.explain）的離線測試：API 一律 mock，不需要網路與 API key。"""
from __future__ import annotations

import csv
import io
import json
import sqlite3
from types import SimpleNamespace

import pytest

from pipeline import config as C
from pipeline import explain as X
from tests.test_pipeline import _neuro_data, _setup_dirs


def _q(qid: str, accepted=("C",), voided=False, **kw) -> dict:
    y, r, s, n = qid.split("-")
    q = {"id": qid, "number": int(n), "stem": f"題幹 {qid}", "options": {k: f"選項{k}" for k in "ABCD"},
         "official_answer": (accepted[0] if accepted else "A"), "accepted_answers": list(accepted), "is_voided": voided,
         "correction_note": None, "has_figure": False, "figure": None, "figure_kind": None,
         "group_id": None, "group_pos": None, "topic": "NEURO-STROKE"}
    q.update(kw)
    return q


def _write_paper(tmp_path, exam_dir: str, subject: str, qs: list[dict]) -> None:
    d = tmp_path / "json" / exam_dir
    d.mkdir(parents=True, exist_ok=True)
    y, r = exam_dir.split("-")
    (d / f"{subject}.json").write_text(json.dumps({"exam": {"id": f"{exam_dir}-{subject}", "roc_year": int(y), "round": int(r),
                                                            "subject_code": subject}, "questions": qs}, ensure_ascii=False), "utf-8")


def _entry(q: dict, status="pass", source="llm", stale=False, **kw) -> dict:
    wo = {k: f"{k} 不對" for k in "ABCD" if k not in q["accepted_answers"]}
    e = {"id": q["id"], "status": status, "source": source, "model": "m", "generated_at": "2026-11-03T00:00:00+00:00",
         "answers_hash": "000000000000" if stale else X.answers_hash(q),
         "why_correct": "因為如此。", "why_others": wo, "takeaway": "記得這個。", "review": {}}
    e.update(kw)
    return e


@pytest.fixture
def dirs(tmp_path, monkeypatch):
    monkeypatch.setattr(C, "JSON_DIR", tmp_path / "json")
    monkeypatch.setattr(C, "EXPLANATION_DIR", tmp_path / "explanations")
    monkeypatch.setattr(C, "DATA_DIR", tmp_path)
    monkeypatch.setattr(C, "FIGURE_DIR", tmp_path / "figures")
    monkeypatch.setattr(C, "EXPLAIN_POLL_SEC", 0)
    return tmp_path


def test_answers_hash_changes_with_correction():
    q = _q("114-2-NEURO-001", accepted=("C",))
    h = X.answers_hash(q)
    assert len(h) == 12 and h == X.answers_hash(dict(q, accepted_answers=["C"]))
    assert h != X.answers_hash(dict(q, accepted_answers=["A", "C"]))
    assert X.answers_hash(dict(q, accepted_answers=["C", "A"])) == X.answers_hash(dict(q, accepted_answers=["A", "C"]))
    assert h != X.answers_hash(dict(q, accepted_answers=[], is_voided=True))


def test_text_len_counts_english_word_as_one():
    assert X.text_len("小洞中風（lacunar stroke）") == 4 + 2 + 2
    assert X.text_len("FEV1 下降 30%") == 1 + 2 + 2
    assert X.text_len("") == 0


def test_select_todo(dirs):
    qs = [_q(f"114-2-NEURO-{i:03d}") for i in range(1, 8)]
    _write_paper(dirs, "114-2", "NEURO", qs)
    _write_paper(dirs, "104-2", "NEURO", [_q("104-2-NEURO-001")])   # EXPLAIN_MIN_YEAR（105）以前不做
    X.save_explanations("114-2", "NEURO", {
        qs[0]["id"]: _entry(qs[0]),                                   # 通過、hash 一致 → 跳過
        qs[1]["id"]: _entry(qs[1], stale=True),                       # 答案被更正 → 重跑
        qs[2]["id"]: _entry(qs[2], source="human", stale=True),       # 人工寫的 → 永遠跳過
        qs[4]["id"]: _entry(qs[4], status="pending"),                 # 還沒審完 → 重跑
        qs[5]["id"]: _entry(qs[5], status="rejected"),                # 不通過、hash 一致 → 不加 --force 不重跑
    })
    ids = lambda todo: [q["id"][-3:] for _, _, q in todo]
    assert ids(X.select_todo()) == ["002", "004", "005", "007"]
    assert ids(X.select_todo(force=True)) == ["001", "002", "004", "005", "006", "007"]
    assert ids(X.select_todo(limit=2)) == ["002", "004"]
    assert ids(X.select_todo(only="104-2/NEURO")) == []
    assert ids(X.select_todo(only="114-2/NEURO", limit=1)) == ["002"]
    assert ids(X.select_todo(years=[113, 114])) == ["002", "004", "005", "007"]
    assert ids(X.select_todo(years=[113])) == []
    assert ids(X.select_todo(years=[109, 114], limit=1)) == ["002"]   # 109 低於門檻，給了也不做


def test_select_format_rejects_after_rule_change(dirs):
    qs = [_q(f"114-2-NEURO-{i:03d}") for i in range(1, 4)]
    _write_paper(dirs, "114-2", "NEURO", qs)
    fmt = lambda q, n, fails: _entry(q, status="rejected", review={"fails": fails},
                                      why_others={"A": "字" * n, "B": "b", "D": "d"})
    X.save_explanations("114-2", "NEURO", {
        qs[0]["id"]: fmt(qs[0], 46, ["format"]),                      # 現在規則下合格、沒審過 → 重跑
        qs[1]["id"]: fmt(qs[1], 60, ["format"]),                      # 還是超過 → 不重跑
        qs[2]["id"]: fmt(qs[2], 46, ["format", "blind_mismatch"]),    # 盲解也錯 → 不重跑
    })
    assert [q["id"][-3:] for _, _, q in X.select_todo()] == ["001"]


def test_judge():
    q = _q("114-2-NEURO-001", accepted=("C",))
    w = {"why_correct": "因為。", "why_others": {"A": "a", "B": "b", "D": "d"}, "takeaway": "記。"}
    ok = {"verdict": "pass", "reason": "ok"}
    assert X.judge(q, w, {"answer": ["C"], "confidence": 0.9}, ok) == ("pass", [])
    assert X.judge(q, w, {"answer": ["B"], "confidence": 0.9}, ok) == ("rejected", ["blind_mismatch"])
    # 盲解多選：要整組都在承認答案裡
    assert X.judge(q, w, {"answer": ["B", "C"], "confidence": 0.5}, ok)[0] == "rejected"
    multi = _q("114-2-NEURO-053", accepted=("A", "B"))
    wm = {"why_correct": "A 與 B 都成立。", "why_others": {"C": "c", "D": "d"}, "takeaway": "記。"}
    assert X.judge(multi, wm, {"answer": ["A"], "confidence": 0.6}, ok) == ("pass", [])
    assert X.judge(multi, wm, {"answer": ["A", "B"], "confidence": 0.6}, ok) == ("pass", [])
    # 送分題不比答案
    voided = _q("114-2-NEURO-043", accepted=(), voided=True)
    wv = {"why_correct": "考選部公告一律給分。題意不清。", "why_others": {}, "takeaway": "記。"}
    assert X.judge(voided, wv, {"answer": ["A"], "confidence": 0.3}, ok) == ("pass", [])
    # verdict 不是 pass
    assert X.judge(q, w, {"answer": ["C"], "confidence": 0.9}, {"verdict": "reasoning_error", "reason": "x"}) == \
        ("rejected", ["reasoning_error"])
    assert X.judge(q, w, {"answer": ["A"], "confidence": 0.9}, {"verdict": "answer_disputed", "reason": "x"}) == \
        ("rejected", ["blind_mismatch", "answer_disputed"])
    # 格式不合格：不送比對、直接不通過
    bad = dict(w, why_others={"A": "a", "B": "b"})
    assert X.judge(q, bad, {"answer": ["C"], "confidence": 0.9}, None) == ("rejected", ["format"])
    # 結果不齊 → pending
    assert X.judge(q, w, None, None) == ("pending", ["missing"])
    assert X.judge(q, w, {"answer": ["C"], "confidence": 0.9}, None) == ("pending", ["missing"])


def test_format_problems():
    q = _q("114-2-NEURO-001", accepted=("C",))
    good = {"why_correct": "因為。", "why_others": {"A": "a", "B": "b", "D": "d"}, "takeaway": "記。"}
    assert X.format_problems(q, good) == []
    assert any("缺選項 D" in p for p in X.format_problems(q, dict(good, why_others={"A": "a", "B": "b"})))
    assert any("正解選項 C" in p for p in X.format_problems(q, dict(good, why_others={**good["why_others"], "C": "c"})))
    assert any("不存在的選項 E" in p for p in X.format_problems(q, dict(good, why_others={**good["why_others"], "E": "e"})))
    # 字數容許超過上限 20%：120 → 144、40 → 48、50 → 60
    assert X.format_problems(q, dict(good, why_correct="字" * 144)) == []
    assert any("why_correct 145 字 > 144" in p for p in X.format_problems(q, dict(good, why_correct="字" * 145)))
    assert any("takeaway" in p for p in X.format_problems(q, dict(good, takeaway="")))
    assert X.format_problems(q, dict(good, takeaway="字" * 60)) == []
    assert any("takeaway" in p for p in X.format_problems(q, dict(good, takeaway="字" * 61)))
    assert X.format_problems(q, dict(good, why_others={**good["why_others"], "A": "字" * 48})) == []
    assert any("why_others A" in p for p in X.format_problems(q, dict(good, why_others={**good["why_others"], "A": "字" * 49})))
    # 多正解：why_correct 上限 160（容許到 192）
    multi = _q("114-2-NEURO-053", accepted=("A", "B"))
    gm = {"why_correct": "字" * 192, "why_others": {"C": "c", "D": "d"}, "takeaway": "記。"}
    assert X.format_problems(multi, gm) == []
    assert any("why_correct 193 字 > 192" in p for p in X.format_problems(multi, dict(gm, why_correct="字" * 193)))
    voided = _q("114-2-NEURO-043", accepted=(), voided=True)
    assert X.format_problems(voided, dict(good, why_others={})) == []


def test_parse_tool_output():
    w, err = X.parse_tool_output("writer", {"why_correct": " 因為 ", "takeaway": "記",
                                            "why_others": [{"option": "a", "reason": "不對"}, {"option": "B", "reason": "也不對"}]})
    assert err is None and w == {"why_correct": "因為", "why_others": {"A": "不對", "B": "也不對"}, "takeaway": "記"}
    assert X.parse_tool_output("writer", {"why_correct": "x", "takeaway": "y",
                                          "why_others": [{"option": "A", "reason": "1"}, {"option": "A", "reason": "2"}]})[1]
    assert X.parse_tool_output("blind", {"answer": ["C", "A", "C"], "confidence": 1.7})[0] == {"answer": ["A", "C"], "confidence": 1.0}
    assert X.parse_tool_output("blind", {"answer": [], "confidence": 0.5})[1]
    assert X.parse_tool_output("check", {"verdict": "maybe", "reason": "?"})[1]
    # 模型偶爾輸出 HTML 跳脫字元
    assert X.parse_tool_output("check", {"verdict": "pass", "reason": "Kleim &amp; Jones"})[0]["reason"] == "Kleim & Jones"


def test_prompts_group_figure_and_option_images(dirs):
    (dirs / "figures").mkdir()
    (dirs / "figures" / "114-2-NEURO-002.webp").write_bytes(b"RIFF....WEBP")
    first = _q("114-2-NEURO-001", group_id="114-2-NEURO-001", group_pos=1, stem="個案資料……請依序回答下列2題。第一問？")
    second = _q("114-2-NEURO-002", group_id="114-2-NEURO-001", group_pos=2, stem="承上，第二問？",
                figure="figures/114-2-NEURO-002.webp", options={k: "" for k in "ABCD"})
    _write_paper(dirs, "114-2", "NEURO", [first, second])
    idx = X.QuestionIndex()
    prev = idx.group_prev(idx.by_id[second["id"]])
    assert [x["id"] for x in prev] == [first["id"]]
    text = X.writer_prompt(second, prev, "腦中風")
    assert "個案資料" in text and "題組的第 2 題" in text and "由上而下依序是 A、B、C、D" in text
    assert "承認答案：C" in text and "主題：腦中風" in text
    blind = X.blind_prompt(second, prev)
    assert "承認答案" not in blind and "答案" not in blind.replace("你的答案", "")
    params = X.request_params("blind", second, blind)
    assert params["messages"][0]["content"][0]["type"] == "image"
    assert params["messages"][0]["content"][0]["source"]["media_type"] == "image/webp"
    assert params["tool_choice"] == {"type": "auto"}               # Opus 5.5 不接受強制 tool_choice
    assert params["tools"][0]["strict"] is True and params["model"] == C.EXPLAIN_MODEL
    # 更正過與送分的答案說明
    corrected = _q("114-2-NEURO-053", accepted=("A", "B"), official_answer="B", correction_note="第53題答Ａ或Ｂ者均給分")
    assert "承認答案：A、B（以上都給分）；原公布答案 B" in X.answer_text(corrected)
    assert "一律給分" in X.answer_text(_q("114-2-NEURO-043", accepted=(), voided=True, official_answer="C"))


# ---------------------------------------------------------------- 假的 Batch API

class FakeBatches:
    """模擬 client.messages.batches：create 存請求、retrieve 立刻 ended、results 依 responder 產生結果（順序打亂）。"""

    def __init__(self, responder):
        self.responder = responder
        self.batches: dict[str, list[dict]] = {}
        self.created = 0

    def create(self, requests):
        self.created += 1
        bid = f"msgbatch_{self.created}"
        self.batches[bid] = requests
        return SimpleNamespace(id=bid, processing_status="in_progress")

    def retrieve(self, bid):
        n = len(self.batches[bid])
        return SimpleNamespace(processing_status="ended",
                               request_counts=SimpleNamespace(succeeded=n, errored=0, canceled=0, expired=0, processing=0))

    def results(self, bid):
        for req in reversed(self.batches[bid]):
            role, qid = req["custom_id"].split("_", 1)
            out = self.responder(role, qid, req["params"])
            if out is None:
                yield SimpleNamespace(custom_id=req["custom_id"],
                                      result=SimpleNamespace(type="errored", error=SimpleNamespace(type="api_error")))
                continue
            tool = req["params"]["tools"][0]["name"]
            msg = SimpleNamespace(model="claude-opus-5-5", stop_reason="tool_use",
                                  usage=SimpleNamespace(input_tokens=100, output_tokens=50,
                                                        cache_creation_input_tokens=0, cache_read_input_tokens=0),
                                  content=[SimpleNamespace(type="thinking"),
                                           SimpleNamespace(type="tool_use", name=tool, input=out)])
            yield SimpleNamespace(custom_id=req["custom_id"], result=SimpleNamespace(type="succeeded", message=msg))


def _fake_client(responder):
    return SimpleNamespace(messages=SimpleNamespace(batches=FakeBatches(responder)))


def _responder(blind_answers: dict[str, list[str]], verdicts: dict[str, str], bad_format: set[str] = frozenset()):
    def respond(role, qid, params):
        if role == "writer":
            others = [{"option": k, "reason": f"{k} 不對"} for k in "ABD"]
            if qid in bad_format:
                others = others[:1]
            return {"why_correct": "因為如此。", "why_others": others, "takeaway": "記得。"}
        if role == "blind":
            return {"answer": blind_answers.get(qid, ["C"]), "confidence": 0.9}
        return {"verdict": verdicts.get(qid, "pass"), "reason": "理由。"}
    return respond


def test_run_end_to_end_with_fake_batches(dirs, capsys):
    qs = [_q(f"114-2-NEURO-{i:03d}") for i in range(1, 6)]
    _write_paper(dirs, "114-2", "NEURO", qs)
    client = _fake_client(_responder({"114-2-NEURO-002": ["A"]}, {"114-2-NEURO-003": "reasoning_error",
                                                                     "114-2-NEURO-004": "answer_disputed"},
                                     bad_format={"114-2-NEURO-005"}))
    state = X.new_state(X.select_todo())
    X.save_state(state)
    X.run(client, state)
    entries = X.load_explanations("114-2", "NEURO")
    st = {k[-3:]: (e["status"], e["review"].get("fails")) for k, e in entries.items()}
    assert st == {"001": ("pass", None), "002": ("rejected", ["blind_mismatch"]), "003": ("rejected", ["reasoning_error"]),
                  "004": ("rejected", ["answer_disputed"]), "005": ("rejected", ["format"])}
    e1 = entries["114-2-NEURO-001"]
    assert e1["why_others"] == {"A": "A 不對", "B": "B 不對", "D": "D 不對"} and e1["answers_hash"] == X.answers_hash(qs[0])
    assert e1["review"] == {"blind_answer": ["C"], "blind_confidence": 0.9, "verdict": "pass", "reason": "理由。"}
    # 格式不合格的題不送比對：第二批只有 4 題
    assert len(client.messages.batches.batches["msgbatch_2"]) == 4
    out = capsys.readouterr().out
    assert "通過 1、不通過 4" in out and "reasoning_error 1、answer_disputed 1、盲解答錯 1、格式不符 1" in out
    assert not (X.state_dir() / "state.json").exists() and list(X.state_dir().glob("run-*.json"))
    # 再跑一次：通過的與 hash 一致的不通過都不再選
    assert X.select_todo() == []


def test_run_resumes_after_interruption(dirs):
    qs = [_q(f"114-2-NEURO-{i:03d}") for i in range(1, 3)]
    _write_paper(dirs, "114-2", "NEURO", qs)
    client = _fake_client(_responder({}, {}))
    state = X.new_state(X.select_todo())
    X.save_state(state)
    # 第一批送出後就斷掉
    bid = client.messages.batches.create(requests=X.build_requests("writer", state["ids"], X.QuestionIndex(), state)
                                         + X.build_requests("blind", state["ids"], X.QuestionIndex(), state)).id
    state["phase1_batch"] = bid
    X.save_state(state)
    # 續跑：不重送第一批
    X.run(client, X.load_state())
    assert client.messages.batches.created == 2
    assert {e["status"] for e in X.load_explanations("114-2", "NEURO").values()} == {"pass"}


def test_run_errored_requests_stay_pending(dirs):
    qs = [_q("114-2-NEURO-001"), _q("114-2-NEURO-002")]
    _write_paper(dirs, "114-2", "NEURO", qs)
    base = _responder({}, {})
    client = _fake_client(lambda role, qid, p: None if (role == "blind" and qid.endswith("002")) else base(role, qid, p))
    state = X.new_state(X.select_todo())
    X.save_state(state)
    X.run(client, state)
    entries = X.load_explanations("114-2", "NEURO")
    assert entries["114-2-NEURO-001"]["status"] == "pass"
    assert entries["114-2-NEURO-002"]["status"] == "pending"
    assert [q["id"] for _, _, q in X.select_todo()] == ["114-2-NEURO-002"]   # 下次會重跑


def test_human_entry_never_overwritten(dirs):
    q = _q("114-2-NEURO-001")
    _write_paper(dirs, "114-2", "NEURO", [q])
    X.save_explanations("114-2", "NEURO", {q["id"]: _entry(q, source="human", why_correct="人工寫的。")})
    assert X.select_todo(force=True) == []
    # 執行途中才被人工改寫：寫回時也不蓋掉
    state = X.new_state([("114-2", "NEURO", q)])
    state["results"] = {"writer": {q["id"]: {"why_correct": "llm", "why_others": {"A": "a", "B": "b", "D": "d"}, "takeaway": "t"}},
                        "blind": {q["id"]: {"answer": ["C"], "confidence": 1}}, "check": {q["id"]: {"verdict": "pass", "reason": ""}}}
    X.write_entries(state, X.QuestionIndex())
    assert X.load_explanations("114-2", "NEURO")[q["id"]]["why_correct"] == "人工寫的。"


def test_dry_run_prints_three_prompts(dirs, capsys):
    _write_paper(dirs, "114-2", "NEURO", [_q("114-2-NEURO-001")])
    X.main(["--only", "114-2/NEURO", "--dry-run"])
    out = capsys.readouterr().out
    assert "會送出 1 題" in out
    assert out.count("[system]") == 3 and "write_explanation" in out and "submit_answer" in out and "submit_review" in out


# ---------------------------------------------------------------- validate／build

def _with_explanations(tmp_path, monkeypatch, mutate):
    data = _neuro_data()
    qs = data["questions"]
    entries = mutate(qs)
    dist_dir, review_dir = _setup_dirs(tmp_path, monkeypatch, data)
    X.save_explanations("108-1", "NEURO", entries)
    return qs, dist_dir, review_dir


def test_validate_explanations(tmp_path, monkeypatch, capsys):
    from pipeline import validate as V

    def mutate(qs):
        return {
            qs[0]["id"]: _entry(qs[0]),                                                      # 合格
            qs[1]["id"]: _entry(qs[1], why_correct="字" * 200),                              # 字數超過
            qs[2]["id"]: _entry(qs[2], why_others={qs[2]["accepted_answers"][0]: "x"}),      # 含正解、缺選項
            qs[3]["id"]: _entry(qs[3], stale=True),                                          # 答案被更正
            qs[4]["id"]: _entry(qs[4], status="rejected", why_correct="字" * 200),          # 不通過的不檢查格式
            "108-1-NEURO-099": _entry(dict(qs[0], id="108-1-NEURO-099")),                    # 題目不存在
        }

    qs, _, review_dir = _with_explanations(tmp_path, monkeypatch, mutate)
    V.main([])
    out = capsys.readouterr().out
    assert f"{qs[1]['id']}: 詳解why_correct 200 字" in out
    assert f"{qs[2]['id']}: 詳解why_others 含正解選項" in out and "缺選項" in out
    assert "108-1-NEURO-099: 詳解對應的題目不存在" in out
    assert qs[0]["id"] + ":" not in out and qs[4]["id"] + ":" not in out
    rows = {r["id"]: r["reasons"] for r in csv.DictReader(io.StringIO((review_dir / "review_queue.csv").read_text("utf-8-sig")))}
    assert "explanation_stale" in rows[qs[3]["id"]]
    assert sum("explanation_stale" in r for r in rows.values()) == 1
    with pytest.raises(SystemExit):
        V.main(["--strict"])


def test_build_only_pass_explanations(tmp_path, monkeypatch):
    from pipeline import build as B

    def mutate(qs):
        return {
            qs[0]["id"]: _entry(qs[0]),
            qs[1]["id"]: _entry(qs[1], status="rejected"),
            qs[2]["id"]: _entry(qs[2], status="pending"),
            qs[3]["id"]: _entry(qs[3], stale=True),            # 答案被更正 → 不收錄
            qs[4]["id"]: _entry(qs[4], source="human"),
        }

    qs, dist_dir, _ = _with_explanations(tmp_path, monkeypatch, mutate)
    B.build("2026.11.03-1", None)
    con = sqlite3.connect(str(dist_dir / "questions.sqlite"))
    rows = con.execute("SELECT question_id, why_others, takeaway FROM explanations ORDER BY question_id").fetchall()
    assert [r[0] for r in rows] == [qs[0]["id"], qs[4]["id"]]
    assert json.loads(rows[0][1]) == {k: f"{k} 不對" for k in "ABCD" if k not in qs[0]["accepted_answers"]}
    assert con.execute("SELECT value FROM meta WHERE key='explanation_count'").fetchone()[0] == "2"
    assert con.execute("SELECT value FROM meta WHERE key='schema_version'").fetchone()[0] == "1"
    manifest = json.loads((dist_dir / "manifest.json").read_text("utf-8"))
    assert manifest["explanation_count"] == 2 and manifest["schema_version"] == 1

    from pipeline.release_notes import collect_stats, render
    stats = collect_stats(dist_dir / "questions.sqlite")
    assert stats["explanations"] == 2
    text = render(dict(manifest, bank_version="2026.11.03-1"), stats)
    assert "**AI 詳解**：2 題" in text
    assert "AI 詳解" not in render(dict(manifest, bank_version="x"), dict(stats, explanations=0))
