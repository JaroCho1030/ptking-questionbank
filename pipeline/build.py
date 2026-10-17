"""把 data/json + taxonomy 打包成 App 用的 SQLite 題庫與 manifest.json。

用法：
    python -m pipeline.build --version 2026.10.16-1 \
        --release-base-url https://github.com/<owner>/ptking-questionbank/releases/download/v2026.10.16-1
產出：
    dist/questions.sqlite   App 內建與遠端更新都用這個檔（含附圖 BLOB）
    dist/manifest.json      App 啟動時先抓這個檔比版本號

版本號規則：YYYY.MM.DD-N（同一天第 N 次），App 端用字串比較（新版 > 舊版）即可，所以要補零。
schema_version 只在資料表結構不相容時才 +1，App 用它決定能不能吃這份題庫。
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

import yaml

from . import config as C

SCHEMA_VERSION = 1

SCHEMA_SQL = """
PRAGMA journal_mode = DELETE;
PRAGMA page_size = 4096;

CREATE TABLE meta (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE TABLE subjects (
    code       TEXT PRIMARY KEY,
    name       TEXT NOT NULL,
    full_name  TEXT NOT NULL,
    short_name TEXT NOT NULL,
    sort_order INTEGER NOT NULL
);

CREATE TABLE topics (
    code         TEXT PRIMARY KEY,
    subject_code TEXT NOT NULL REFERENCES subjects(code),
    name         TEXT NOT NULL,
    grp          TEXT,
    sort_order   INTEGER NOT NULL
);

CREATE TABLE exams (
    id             TEXT PRIMARY KEY,   -- 108-1-NEURO
    roc_year       INTEGER NOT NULL,
    round          INTEGER NOT NULL,
    subject_code   TEXT NOT NULL REFERENCES subjects(code),
    paper_code     TEXT,
    question_count INTEGER NOT NULL,
    source_q       TEXT,
    source_s       TEXT,
    source_m       TEXT
);

CREATE TABLE questions (
    id               TEXT PRIMARY KEY,   -- 108-1-NEURO-023
    exam_id          TEXT NOT NULL REFERENCES exams(id),
    subject_code     TEXT NOT NULL REFERENCES subjects(code),
    topic_code       TEXT REFERENCES topics(code),
    number           INTEGER NOT NULL,
    stem             TEXT NOT NULL,
    option_a         TEXT NOT NULL,
    option_b         TEXT NOT NULL,
    option_c         TEXT NOT NULL,
    option_d         TEXT NOT NULL,
    accepted_answers TEXT NOT NULL,      -- "D" 或 "A,C"；送分題為 ""
    official_answer  TEXT,
    is_voided        INTEGER NOT NULL DEFAULT 0,
    correction_note  TEXT,
    has_figure       INTEGER NOT NULL DEFAULT 0,
    tags             TEXT NOT NULL DEFAULT '[]'   -- JSON array
);
CREATE INDEX idx_questions_exam    ON questions(exam_id, number);
CREATE INDEX idx_questions_subject ON questions(subject_code);
CREATE INDEX idx_questions_topic   ON questions(topic_code);

CREATE TABLE figures (
    question_id TEXT PRIMARY KEY REFERENCES questions(id),
    png         BLOB NOT NULL,
    kind        TEXT
);

-- 全文搜尋：trigram 讓中文子字串搜尋不用斷詞；iOS 17 內建 SQLite 支援 FTS5 + trigram
CREATE VIRTUAL TABLE questions_fts USING fts5(
    id UNINDEXED, stem, option_a, option_b, option_c, option_d,
    tokenize = 'trigram'
);
"""


def _sha256_file(p: Path) -> str:
    h = hashlib.sha256()
    with p.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def build(version: str, release_base_url: str | None) -> None:
    C.DIST_DIR.mkdir(parents=True, exist_ok=True)
    db_path = C.DIST_DIR / "questions.sqlite"
    if db_path.exists():
        db_path.unlink()
    tax = yaml.safe_load(C.TAXONOMY_PATH.read_text("utf-8"))

    con = sqlite3.connect(str(db_path))
    con.executescript(SCHEMA_SQL)

    for s in C.SUBJECTS:
        tax_s = next(x for x in tax["subjects"] if x["code"] == s["code"])
        con.execute("INSERT INTO subjects VALUES (?,?,?,?,?)",
                    (s["code"], tax_s["name"], tax_s["full_name"], tax_s.get("short_name", tax_s["name"]), s["sort"]))
        for i, t in enumerate(tax_s["topics"], start=1):
            con.execute("INSERT INTO topics VALUES (?,?,?,?,?)", (t["code"], s["code"], t["name"], t.get("group"), i))

    exams_summary = []
    q_total = 0
    for p in sorted(C.JSON_DIR.glob("*/*.json")):
        data = json.loads(p.read_text("utf-8"))
        ex, qs = data["exam"], data["questions"]
        con.execute("INSERT INTO exams VALUES (?,?,?,?,?,?,?,?,?)",
                    (ex["id"], ex["roc_year"], ex["round"], ex["subject_code"], ex.get("paper_code"),
                     len(qs), ex.get("source_q"), ex.get("source_s"), ex.get("source_m")))
        for q in qs:
            con.execute(
                "INSERT INTO questions VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (q["id"], ex["id"], ex["subject_code"], q.get("topic"), q["number"], q["stem"],
                 q["options"]["A"], q["options"]["B"], q["options"]["C"], q["options"]["D"],
                 ",".join(q["accepted_answers"]), q.get("official_answer"), int(q["is_voided"]),
                 q.get("correction_note"), int(bool(q.get("figure"))), json.dumps(q.get("tags") or [], ensure_ascii=False)))
            con.execute("INSERT INTO questions_fts VALUES (?,?,?,?,?,?)",
                        (q["id"], q["stem"], q["options"]["A"], q["options"]["B"], q["options"]["C"], q["options"]["D"]))
            if q.get("figure"):
                fig = C.DATA_DIR / q["figure"]
                if fig.exists():
                    con.execute("INSERT INTO figures VALUES (?,?,?)", (q["id"], fig.read_bytes(), q.get("figure_kind")))
        exams_summary.append({"id": ex["id"], "roc_year": ex["roc_year"], "round": ex["round"],
                              "subject_code": ex["subject_code"], "question_count": len(qs)})
        q_total += len(qs)

    built_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
    meta = {
        "schema_version": str(SCHEMA_VERSION), "bank_version": version, "built_at": built_at,
        "question_count": str(q_total), "taxonomy_version": str(tax.get("version", 1)),
        "source": "考選部考畢試題查詢平臺 https://wwwq.moex.gov.tw/exam/wFrmExamQandASearch.aspx",
    }
    con.executemany("INSERT INTO meta VALUES (?,?)", meta.items())
    con.commit()
    con.execute("VACUUM")
    con.close()

    latest = max(exams_summary, key=lambda e: (e["roc_year"], e["round"]), default=None)
    manifest = {
        "schema_version": SCHEMA_VERSION,
        "bank_version": version,
        "released_at": built_at,
        "question_count": q_total,
        "latest_exam": f"{latest['roc_year']}-{latest['round']}" if latest else None,
        "min_app_version": "1.0.0",
        "sqlite": {
            "file": "questions.sqlite",
            "url": f"{release_base_url.rstrip('/')}/questions.sqlite" if release_base_url else None,
            "sha256": _sha256_file(db_path),
            "size": db_path.stat().st_size,
        },
        "exams": exams_summary,
    }
    (C.DIST_DIR / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), "utf-8")
    print(f"questions.sqlite {db_path.stat().st_size / 1e6:.1f} MB，{q_total} 題，版本 {version}")
    print(f"manifest → {C.DIST_DIR / 'manifest.json'}")


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--version", default=os.environ.get("BANK_VERSION") or datetime.now().strftime("%Y.%m.%d-1"))
    ap.add_argument("--release-base-url", default=os.environ.get("RELEASE_BASE_URL"))
    args = ap.parse_args(argv)
    build(args.version, args.release_base_url)


if __name__ == "__main__":
    main()
