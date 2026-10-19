"""產生 GitHub Release 的對外說明 dist/RELEASE_NOTES.md（給考生與一般人看）。

用法（在 build 之後）：
    python -m pipeline.release_notes [--previous-manifest prev_manifest.json]

內容全部從 dist/questions.sqlite 與 dist/manifest.json 算出來，不用手寫；
有上一版的 manifest 時列出新增的試卷，沒有就當成第一版。
"""
from __future__ import annotations

import argparse
import json
import os
import sqlite3
from pathlib import Path

from . import config as C

SOURCE_URL = "https://wwwq.moex.gov.tw/exam/wFrmExamQandASearch.aspx"
ROUND_NAME = {1: "第一次", 2: "第二次"}


def _exam_label(roc_year: int, rnd: int) -> str:
    return f"{roc_year} 年{ROUND_NAME.get(rnd, f'第{rnd}次')}"


def collect_stats(db_path: Path) -> dict:
    con = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    one = lambda sql: con.execute(sql).fetchone()[0]
    sessions = con.execute("SELECT DISTINCT roc_year, round FROM exams ORDER BY roc_year, round").fetchall()
    stats = {
        "question_count": one("SELECT COUNT(*) FROM questions"),
        "paper_count": one("SELECT COUNT(*) FROM exams"),
        "sessions": sessions,
        "subjects": con.execute(
            "SELECT s.name, COUNT(q.id) FROM subjects s LEFT JOIN questions q ON q.subject_code = s.code "
            "GROUP BY s.code ORDER BY s.sort_order").fetchall(),
        "corrected": one("SELECT COUNT(*) FROM questions WHERE COALESCE(correction_note, '') <> ''"),
        "voided": one("SELECT COUNT(*) FROM questions WHERE is_voided = 1"),
        "figures": one("SELECT COUNT(*) FROM questions WHERE has_figure = 1"),
        "topics": one("SELECT COUNT(*) FROM topics"),
    }
    con.close()
    return stats


def _changes(manifest: dict, previous: dict | None) -> list[str]:
    if not previous:
        return ["第一版發布。"]
    old_ids = {e["id"] for e in previous.get("exams", [])}
    new = [e for e in manifest["exams"] if e["id"] not in old_ids]
    lines = []
    if new:
        by_session: dict[tuple, list] = {}
        for e in new:
            by_session.setdefault((e["roc_year"], e["round"]), []).append(e)
        for (y, r), es in sorted(by_session.items()):
            lines.append(f"新增 {_exam_label(y, r)}試題：{len(es)} 科、{sum(e['question_count'] for e in es):,} 題。")
    else:
        lines.append("收錄的考試不變，修正題目內容、答案或主題分類。")
    lines.append(f"上一版：{previous.get('bank_version', '?')}（{previous.get('question_count', 0):,} 題）。")
    return lines


def render(manifest: dict, stats: dict, previous: dict | None = None,
           repo_url: str = "https://github.com/JaroCho1030/ptking-questionbank") -> str:
    first, last = stats["sessions"][0], stats["sessions"][-1]
    subj_rows = "\n".join(f"| {name} | {n:,} |" for name, n in stats["subjects"])
    changes = "\n".join(f"- {line}" for line in _changes(manifest, previous))
    voided = f"；其中 {stats['voided']} 題一律給分，練習時不計分" if stats["voided"] else ""
    return f"""這是「我要成為物治王」App 使用的**物理治療師國考題庫**，版本 {manifest['bank_version']}。

**收錄範圍**：民國 {_exam_label(*first)}至 {_exam_label(*last)}，共 {len(stats['sessions'])} 次考試、{stats['paper_count']} 份試卷、**{stats['question_count']:,} 題**選擇題，每題附考選部公布的答案。

### 本版更新
{changes}

### 各科題數
| 科目 | 題數 |
|---|---:|
{subj_rows}

### 題目資料
- **答案更正**：考選部事後更正答案的 {stats['corrected']} 題，已改用更正後的答案{voided}。
- **附圖**：{stats['figures']} 題附有原試卷的圖片。
- **主題分類**：每題依考科內容歸到 {stats['topics']} 個主題之一（少數題目另有次要主題），方便按主題練習。分類由 AI 輔助、再經人工抽查修正，僅供參考。

### 怎麼使用
這份題庫是給「我要成為物治王」iOS App 用的，App 即將在 App Store 上架。App 會自動下載最新版題庫，**不需要手動下載下面的檔案**。

### 回報錯誤
發現題目、答案或附圖有誤，歡迎[開 issue]({repo_url}/issues) 並附上題目編號，例如 `108-1-NEURO-027` 代表 108 年第一次考試、神經疾病物理治療學第 27 題。

### 資料來源與聲明
試題與答案取自考選部[考畢試題查詢平臺]({SOURCE_URL})公開資料。本專案為個人製作，與考選部無關；內容如與考選部公告不一致，以考選部公告為準。

<details>
<summary>檔案說明（開發者）</summary>

- `questions.sqlite`：SQLite 題庫（schema 第 {manifest['schema_version']} 版），sha256 `{manifest['sqlite']['sha256']}`
- `manifest.json`：版本號、題數與檔案 sha256，App 用它檢查更新
- 固定網址：`{repo_url}/releases/latest/download/manifest.json`
</details>
"""


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--previous-manifest", type=Path, help="上一版的 manifest.json；沒給就當第一版")
    ap.add_argument("--out", type=Path, default=C.DIST_DIR / "RELEASE_NOTES.md")
    args = ap.parse_args(argv)
    manifest = json.loads((C.DIST_DIR / "manifest.json").read_text("utf-8"))
    previous = None
    if args.previous_manifest and args.previous_manifest.exists():
        previous = json.loads(args.previous_manifest.read_text("utf-8"))
    repo = os.environ.get("GITHUB_REPOSITORY", "JaroCho1030/ptking-questionbank")
    text = render(manifest, collect_stats(C.DIST_DIR / "questions.sqlite"), previous, f"https://github.com/{repo}")
    args.out.write_text(text, "utf-8")
    print(f"release notes → {args.out}")


if __name__ == "__main__":
    main()
