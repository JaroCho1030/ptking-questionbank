"""從考選部「考畢試題查詢平臺」抓物理治療師各科的試題 (Q)、答案 (S)、更正答案 (M) PDF。

用法：
    python -m pipeline.fetch --years 105-115            # 抓缺的試題；最近 4 回合的答案一律重抓比對
    python -m pipeline.fetch --years 113 --refresh all  # 指定年份所有回合答案都重抓
    python -m pipeline.fetch --years 105-115 --dry-run  # 只列出會抓什麼

重點：考選部公布答案後可能再公告「更正答案」(t=M)，甚至更正不只一次，所以：
  1. 每次執行都會重新掃描「最近 N 回合」的結果頁，看有沒有新出現的更正答案連結；
  2. 答案 PDF (S/M) 很小，對這些回合一律重新下載並比對 sha256；
  3. 有變動就寫進 data/raw/changes.log，並在 index.json 標記 answers_changed=true，
     parse 階段看到這個旗標會強制重新解析該科。

網址規則（2026-10 觀察）：
  結果頁  wFrmExamQandASearch.aspx?y=<西元年>&e=<考試代碼>
  檔案    wHandExamQandA_File.ashx?t=<Q|S|M>&code=<考試代碼>&c=<類科代碼>&s=<科目代碼>&q=1
  考試代碼每年不同（108030、113020、115090…），科目代碼也不固定（11/22/…或 0701/0702/…），
  所以一律從頁面解析，不寫死。
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import requests
from bs4 import BeautifulSoup

from . import config as C

INDEX_PATH = C.RAW_DIR / "index.json"
CHANGES_LOG = C.RAW_DIR / "changes.log"

_ROUND_RE = re.compile(r"(\d{3})\s*年\s*第\s*([一二三四])\s*次")
# 考試名稱有時把幾個治療師合寫：「物理治療師」或「物理職能呼吸治療師」（105 年）
_PT_EXAM_RE = re.compile(r"物理[^、，,\s]{0,8}治療")
_CN_NUM = {"一": 1, "二": 2, "三": 3, "四": 4}
_CHK_RE = re.compile(r"chk_(\d+)_(\d+)_(\d+)$")


def _session() -> requests.Session:
    s = requests.Session()
    s.headers["User-Agent"] = C.USER_AGENT
    return s


def _get(sess: requests.Session, url: str, **params) -> requests.Response:
    time.sleep(C.REQUEST_DELAY_SEC)
    r = sess.get(url, params=params or None, timeout=60)
    r.raise_for_status()
    return r


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


# ---------------------------------------------------------------- 頁面解析（純函式，方便測試）

def parse_exam_options(html: str) -> list[dict]:
    """從年度頁面的「考試簡稱」下拉選單找出含物理治療師的考試。

    回傳 [{"exam_code": "108030", "roc_year": 108, "round": 1, "title": "..."}]
    """
    soup = BeautifulSoup(html, "html.parser")
    sel = soup.find("select", id=re.compile(r"ddlExamCode$"))
    out = []
    if not sel:
        return out
    for opt in sel.find_all("option"):
        code = (opt.get("value") or "").strip()
        title = opt.get_text(" ", strip=True)
        if not code or not _PT_EXAM_RE.search(title):
            continue
        m = _ROUND_RE.search(title)
        if m:
            roc_year, rnd = int(m.group(1)), _CN_NUM[m.group(2)]
        else:
            # 沒寫第幾次：當作第 1 次，但要人工確認
            ym = re.match(r"(\d{3})\s*年", title)
            roc_year, rnd = (int(ym.group(1)) if ym else 0), 1
            print(f"[warn] 考試名稱沒有「第X次」，暫定第 1 次：{code} {title}", file=sys.stderr)
        out.append({"exam_code": code, "roc_year": roc_year, "round": rnd, "title": title})
    return out


def parse_subject_rows(html: str, exam_code: str) -> list[dict]:
    """從結果頁找出物理治療師類科底下每一科的 c/s 代碼與可用檔案。

    回傳 [{"subject_code": "NEURO", "subject_name": "...", "class_code": "311",
            "s": "44", "has_q": True, "has_s": True, "has_m": False}]
    """
    soup = BeautifulSoup(html, "html.parser")
    table = soup.find("table", class_="ULQandA")
    if not table:
        return []
    out = []
    current_class = ""
    tbody = table.find("tbody") or table
    for tr in tbody.find_all("tr", recursive=False):
        checkboxes = [cb for cb in tr.find_all("input", type="checkbox") if _CHK_RE.search(cb.get("id", ""))]
        if not checkboxes:
            current_class = tr.get_text(" ", strip=True)
            continue
        if C.CLASS_NAME_PATTERN not in current_class:
            continue
        for cb in checkboxes:
            m = _CHK_RE.search(cb["id"])
            code, c, s = m.group(1), m.group(2), m.group(3)
            if code != exam_code:
                continue
            label = tr.find("label", attrs={"for": cb["id"]})
            name = label.get_text(" ", strip=True) if label else ""
            subj = C.subject_code_for(name)
            if not subj:
                print(f"[warn] 無法對應科目名稱：{name!r}（{exam_code} c={c} s={s}）", file=sys.stderr)
                continue
            hrefs = " ".join(a.get("href", "") for a in tr.find_all("a"))

            def has(t: str) -> bool:
                return f"t={t}&code={code}&c={c}&s={s}" in hrefs.replace("&amp;", "&")

            out.append({
                "subject_code": subj, "subject_name": name, "class_code": c, "s": s,
                "has_q": has("Q"), "has_s": has("S"), "has_m": has("M"),
            })
    # 同一科可能出現兩次（手機版/桌機版連結在同一列，所以不會；但保險去重）
    seen, uniq = set(), []
    for r in out:
        if r["subject_code"] not in seen:
            seen.add(r["subject_code"])
            uniq.append(r)
    return uniq


def file_url(t: str, exam_code: str, class_code: str, s: str) -> str:
    return f"{C.FILE_URL}?t={t}&code={exam_code}&c={class_code}&s={s}&q=1"


# ---------------------------------------------------------------- 主流程

def load_index() -> dict:
    if INDEX_PATH.exists():
        return json.loads(INDEX_PATH.read_text("utf-8"))
    return {"exams": {}}


def save_index(index: dict) -> None:
    INDEX_PATH.parent.mkdir(parents=True, exist_ok=True)
    INDEX_PATH.write_text(json.dumps(index, ensure_ascii=False, indent=2), "utf-8")


def _log_change(msg: str) -> None:
    CHANGES_LOG.parent.mkdir(parents=True, exist_ok=True)
    with CHANGES_LOG.open("a", encoding="utf-8") as f:
        f.write(f"{_now()} {msg}\n")
    print("[change] " + msg)


def _download(sess, url: str, dest: Path, dry_run: bool) -> dict | None:
    if dry_run:
        print(f"  would download {url} -> {dest}")
        return None
    r = _get(sess, url)
    data = r.content
    if not data.startswith(b"%PDF"):
        print(f"[warn] 不是 PDF（{len(data)} bytes）：{url}", file=sys.stderr)
        return None
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_bytes(data)
    return {"path": str(dest.relative_to(C.ROOT)), "sha256": _sha256(data), "size": len(data), "fetched_at": _now(), "url": url}


def discover(sess, roc_years: list[int]) -> list[dict]:
    exams = []
    for y in roc_years:
        html = _get(sess, C.SEARCH_URL, y=y + 1911).text
        found = parse_exam_options(html)
        found = [e for e in found if e["roc_year"] == y]
        if not found:
            print(f"[info] {y} 年沒有找到含物理治療師的考試（可能尚未舉行或頁面改版）")
        exams.extend(found)
    exams.sort(key=lambda e: (e["roc_year"], e["round"]))
    return exams


def fetch(roc_years: list[int], refresh: str, refresh_rounds: int, dry_run: bool) -> None:
    sess = _session()
    index = load_index()
    exams = discover(sess, roc_years)
    if not exams:
        print("沒有可抓的考試。")
        return
    recent_keys = {C.exam_dirname(e["roc_year"], e["round"]) for e in exams[-refresh_rounds:]}

    for e in exams:
        key = C.exam_dirname(e["roc_year"], e["round"])
        entry = index["exams"].setdefault(key, {"exam_code": e["exam_code"], "title": e["title"], "subjects": {}})
        entry["exam_code"], entry["title"] = e["exam_code"], e["title"]
        do_refresh = refresh == "all" or (refresh == "recent" and key in recent_keys)

        # 已完整抓過且不需 refresh → 跳過結果頁，省請求
        complete = len(entry["subjects"]) == len(C.SUBJECTS) and all(
            sub.get("q") and sub.get("s_file") for sub in entry["subjects"].values())
        if complete and not do_refresh:
            print(f"{key}: 已完整，略過")
            continue

        print(f"{key}: 掃描結果頁 exam_code={e['exam_code']}")
        html = _get(sess, C.SEARCH_URL, y=e["roc_year"] + 1911, e=e["exam_code"]).text
        rows = parse_subject_rows(html, e["exam_code"])
        if len(rows) != len(C.SUBJECTS):
            print(f"[warn] {key} 只找到 {len(rows)} 科：{[r['subject_code'] for r in rows]}", file=sys.stderr)

        for row in rows:
            subj = row["subject_code"]
            sub = entry["subjects"].setdefault(subj, {})
            sub.update({"subject_name": row["subject_name"], "class_code": row["class_code"], "s": row["s"]})
            base = C.RAW_DIR / key / subj

            # Q：缺才抓
            if row["has_q"] and not sub.get("q"):
                info = _download(sess, file_url("Q", e["exam_code"], row["class_code"], row["s"]), base.with_suffix(".Q.pdf"), dry_run)
                if info:
                    sub["q"] = info
                    sub["needs_parse"] = True

            # S / M：缺或 refresh 時抓，並比對雜湊
            for t, k in (("S", "s_file"), ("M", "m_file")):
                if not row[f"has_{t.lower()}"]:
                    if t == "M" and sub.get(k):
                        # 頁面上更正連結消失了（罕見），保留舊檔但記錄
                        _log_change(f"{key}/{subj}: 結果頁已無更正答案連結，保留舊 M 檔")
                    continue
                if sub.get(k) and not do_refresh:
                    continue
                info = _download(sess, file_url(t, e["exam_code"], row["class_code"], row["s"]), base.with_suffix(f".{t}.pdf"), dry_run)
                if not info:
                    continue
                old = sub.get(k)
                if old and old.get("sha256") != info["sha256"]:
                    _log_change(f"{key}/{subj}: {t} 檔內容變更 {old['sha256'][:8]} -> {info['sha256'][:8]}")
                    sub["answers_changed"] = True
                    sub["needs_parse"] = True
                elif not old:
                    if t == "M":
                        _log_change(f"{key}/{subj}: 新增更正答案檔")
                    sub["needs_parse"] = True
                sub[k] = info

        if not dry_run:
            save_index(index)
    print("完成。index:", INDEX_PATH)


def parse_years(spec: str) -> list[int]:
    years: set[int] = set()
    for part in spec.split(","):
        part = part.strip()
        if "-" in part:
            a, b = part.split("-", 1)
            years.update(range(int(a), int(b) + 1))
        elif part:
            years.add(int(part))
    return sorted(years)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--years", default="105-115", help="民國年，例如 105-115 或 113,114")
    ap.add_argument("--refresh", choices=["none", "recent", "all"], default="recent",
                    help="答案檔重抓策略（預設 recent：最近 N 回合）")
    ap.add_argument("--refresh-rounds", type=int, default=4, help="recent 模式要重抓幾個回合（預設 4）")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args(argv)
    fetch(parse_years(args.years), args.refresh, args.refresh_rounds, args.dry_run)


if __name__ == "__main__":
    main()
