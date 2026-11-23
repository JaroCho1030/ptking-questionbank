"""對戰題目池規則、輸出與舊版題庫相容性。"""
import copy
import hashlib
import json
import sqlite3
from pathlib import Path

import pytest
import yaml

from pipeline import build as B, config as C


SUBJECT_CODES = ["BASIC", "INTRO", "TECH", "NEURO", "ORTHO", "CARDPED"]
# 實作前的 schema 與所有 table_info 快照雜湊，不能由待測版 DDL 產生預期值。
BASE_SCHEMA_SHA256 = "8b5a46e4398ac9c2bc1de3e92faae04d846e660e38a5629c6d56c7bf3a0eaab5"


def question(**changes):
    q = {"id": "108-1-NEURO-001", "number": 1, "stem": "題幹",
         "options": {"A": "甲", "B": "乙", "C": "丙", "D": "丁"},
         "accepted_answers": ["A"], "is_voided": False, "has_figure": False,
         "figure": None, "group_id": None}
    q.update(changes)
    return q


@pytest.mark.parametrize("changes", [
    {"group_id": "108-1-NEURO-001"}, {"group_id": ""},
    {"is_voided": True}, {"has_figure": True, "figure": None},
    {"stem": "題" * 147},
])
def test_exclusions_are_independent(changes):
    assert B.is_battle_eligible(question())
    assert not B.is_battle_eligible(question(**changes))


@pytest.mark.parametrize("length,eligible", [(150, True), (151, False)])
@pytest.mark.parametrize("field", ["stem", "A", "B", "C", "D"])
def test_total_length_boundary(length, eligible, field):
    q = question(stem="", options={k: "" for k in "ABCD"})
    target = q if field == "stem" else q["options"]
    target[field] = "字" * length
    assert B.is_battle_eligible(q) is eligible


@pytest.mark.parametrize("text", ["English word", "12345", " \n\t", "e\u0301", "😀"])
def test_raw_python_length(text):
    q = question(stem="字" * (150 - len(text)),
                 options={"A": text, "B": "", "C": "", "D": ""})
    assert B.is_battle_eligible(q)
    q["options"]["D"] = " "
    assert not B.is_battle_eligible(q)


def test_answers_preserved_and_updated_without_mutating_input():
    q = question(accepted_answers=["C", "A"])
    before = copy.deepcopy(q)
    pool = B.make_battle_pool("v1", [("NEURO", q)])
    assert pool["questions"] == [{"id": q["id"], "accepted_answers": ["C", "A"], "chars": 6}]
    assert q == before
    pool["questions"][0]["accepted_answers"].append("D")
    assert q == before
    q["accepted_answers"] = ["B", "D"]
    assert B.make_battle_pool("v2", [("NEURO", q)])["questions"] == [
        {"id": q["id"], "accepted_answers": ["B", "D"], "chars": 6}]


def test_order_counts_and_exam_subject():
    entries = [
        ("NEURO", question(id="108-1-NEURO-002", topic="BASIC-ANA-NEU")),
        ("BASIC", question(id="108-1-BASIC-001")),
        ("NEURO", question()),
        ("ORTHO", question(id="108-1-ORTHO-001", is_voided=True)),
    ]
    before = copy.deepcopy(entries)
    pool = B.make_battle_pool("version", iter(entries))
    assert entries == before
    assert pool == {"bank_version": "version", "max_chars": 150, "question_count": 3,
                    "subject_counts": dict(zip(SUBJECT_CODES, [1, 0, 0, 2, 0, 0])),
                    "questions": [{"id": qid, "accepted_answers": ["A"], "chars": 6} for qid in
                                  ["108-1-BASIC-001", "108-1-NEURO-001", "108-1-NEURO-002"]]}
    assert list(pool["subject_counts"]) == SUBJECT_CODES
    assert sum(pool["subject_counts"].values()) == pool["question_count"]


def test_empty_pool():
    pool = B.make_battle_pool("empty", [])
    assert pool["questions"] == [] and pool["question_count"] == 0
    assert list(pool["subject_counts"]) == SUBJECT_CODES
    assert list(pool["subject_counts"].values()) == [0] * 6


def test_build_preserves_existing_formats(tmp_path, monkeypatch):
    for name, path in [("JSON_DIR", tmp_path / "json"), ("DIST_DIR", tmp_path / "dist"),
                       ("DATA_DIR", tmp_path), ("FIGURE_DIR", tmp_path / "figures"),
                       ("EXPLANATION_DIR", tmp_path / "explanations")]:
        monkeypatch.setattr(C, name, path)
    paper_dir = C.JSON_DIR / "108-1"
    paper_dir.mkdir(parents=True)
    qs = [question(number=i, id=f"108-1-NEURO-{i:03d}", **changes)
          for i, changes in enumerate([
              {"accepted_answers": ["C", "A"]}, {"group_id": "108-1-NEURO-002"},
              {"is_voided": True, "accepted_answers": []}, {"has_figure": True},
              {"stem": "字" * 147}, {"stem": "字" * 146},
          ], 1)]
    paper = {"exam": {"id": "108-1-NEURO", "roc_year": 108, "round": 1,
                      "subject_code": "NEURO"}, "questions": list(reversed(qs))}
    (paper_dir / "NEURO.json").write_text(json.dumps(paper, ensure_ascii=False), "utf-8")
    B.build("2026.11.04-1", "https://example.com/release")
    pool = json.loads((C.DIST_DIR / "battle_pool.json").read_text("utf-8"))
    assert pool == {
        "bank_version": "2026.11.04-1", "max_chars": 150, "question_count": 2,
        "subject_counts": dict(zip(SUBJECT_CODES, [0, 0, 0, 2, 0, 0])),
        "questions": [{"id": qs[0]["id"], "accepted_answers": ["C", "A"], "chars": 6},
                      {"id": qs[5]["id"], "accepted_answers": ["A"], "chars": 150}],
    }
    assert list(pool["subject_counts"]) == SUBJECT_CODES
    manifest = json.loads((C.DIST_DIR / "manifest.json").read_text("utf-8"))
    assert set(manifest) == {"schema_version", "bank_version", "released_at", "question_count",
                             "explanation_count", "latest_exam", "min_app_version", "sqlite", "exams"}
    assert set(manifest["sqlite"]) == {"file", "url", "sha256", "size"}
    assert set(manifest["exams"][0]) == {"id", "roc_year", "round", "subject_code", "question_count"}
    assert manifest["bank_version"] == pool["bank_version"]
    assert manifest["schema_version"] == B.SCHEMA_VERSION == 1
    assert manifest["question_count"] == 6
    catalog = json.loads((C.DIST_DIR / "question_catalog.json").read_text("utf-8"))
    assert catalog == {"bank_version": "2026.11.04-1", "question_count": 6,
                       "questions": [{"id": q["id"], "option_count": 4} for q in qs]}
    db = C.DIST_DIR / "questions.sqlite"
    assert manifest["sqlite"]["sha256"] == hashlib.sha256(db.read_bytes()).hexdigest()
    assert manifest["sqlite"]["size"] == db.stat().st_size
    with sqlite3.connect(db) as con:
        assert con.execute("SELECT id FROM questions ORDER BY id").fetchall() == [(q["id"],) for q in qs]
        assert con.execute("SELECT value FROM meta WHERE key='schema_version'").fetchone() == ("1",)
        schema = con.execute("SELECT type,name,tbl_name,sql FROM sqlite_master ORDER BY type,name").fetchall()
        columns = {name: con.execute(f'PRAGMA table_info("{name}")').fetchall()
                   for (name,) in con.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")}
    snapshot = json.dumps({"schema": schema, "columns": columns}, ensure_ascii=False, sort_keys=True)
    assert hashlib.sha256(snapshot.encode()).hexdigest() == BASE_SCHEMA_SHA256


def test_release_includes_all_build_artifacts():
    path = Path(__file__).resolve().parents[1] / ".github/workflows/release.yml"
    workflow = yaml.safe_load(path.read_text("utf-8"))
    release = next(step for step in workflow["jobs"]["build"]["steps"]
                   if step.get("uses", "").startswith("softprops/action-gh-release@"))
    assert release["with"]["files"].splitlines() == [
        "dist/questions.sqlite", "dist/manifest.json", "dist/battle_pool.json",
        "dist/question_catalog.json"]
