"""AI 詳解的子代理分批模式（不需要 API key；規則、格式與通過條件同 pipeline.explain）。

一題三個角色（寫手、盲解、比對）各自是一個子代理批次，盲解的批次檔裡沒有答案與詳解：

    python -m pipeline.explain_batches --export DIR --years 110-112 [--only 110-1/NEURO] [--limit N] [--force] [--batch-size 40]
        → DIR/manifest.json、DIR/figures/<id>.png（附圖轉 PNG 給子代理看）、
          DIR/writer/<批次>.md、DIR/blind/<批次>.md
          子代理讀批次檔，把結果寫成 DIR/results/writer/<批次>.json、DIR/results/blind/<批次>.json
    python -m pipeline.explain_batches --export-check DIR
        → 寫手格式合格、盲解有結果的題 → DIR/check/<批次>.md → DIR/results/check/<批次>.json
    python -m pipeline.explain_batches --import DIR [--model 標示]
        → 三種結果合併、照 explain.judge 判定（通過＝格式合格、盲解答案 ⊆ 承認答案、verdict = pass），寫進 data/explanations

批次名 <年-次>_<科目>_<序號>，每批最多 --batch-size 題；結果 JSON 都是 {"batch": "...", "results": [...]}，
缺題、多題、格式不對都只列警告，不寫入那一題（重跑那份批次即可）。
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from . import config as C
from . import explain as X

ROLE_DIRS = ("writer", "blind", "check")
DEFAULT_MODEL = "subagent writer=sonnet review=opus"


# ---------------------------------------------------------------- 批次檔

def chunk_name(exam_dir: str, subject: str, k: int) -> str:
    return f"{exam_dir}_{subject}_{k}"


def figure_png(q: dict, out_dir: Path) -> Path | None:
    """附圖 WebP 轉成 PNG 放 out_dir/figures/<id>.png（子代理的 Read 工具看 PNG）。沒圖或轉不了回傳 None。"""
    if not q.get("figure"):
        return None
    src = (C.DATA_DIR / q["figure"]).resolve()
    if not src.is_relative_to(C.FIGURE_DIR.resolve()) or not src.exists():
        return None
    dest = out_dir / "figures" / f"{q['id']}.png"
    if not dest.exists():
        from PIL import Image
        dest.parent.mkdir(parents=True, exist_ok=True)
        with Image.open(src) as im:
            im.save(dest, "PNG")
    return dest


def figure_line(q: dict, out_dir: Path) -> str:
    p = figure_png(q, out_dir)
    return f"（本題附圖：{p.resolve()} ——先用 Read 工具看這張圖再作答）\n" if p else ""


def result_path(out_dir: Path, role: str, name: str) -> Path:
    return out_dir / "results" / role / f"{name}.json"


def closing(role: str, name: str, n: int, out_path: Path) -> str:
    sample = {
        "writer": '{"id": "題目 id", "why_correct": "…", "why_others": {"A": "…", "B": "…"}, "takeaway": "…"}',
        "blind": '{"id": "題目 id", "answer": ["C"], "confidence": 0.0–1.0}',
        "check": '{"id": "題目 id", "verdict": "pass｜reasoning_error｜answer_disputed", "reason": "一句理由"}',
    }[role]
    extra = {
        "writer": "why_others 是物件，key 是非正解的選項字母、value 是那一句；正解選項不要放進去，非正解選項一個都不能漏（送分題可以是空物件 {}）。",
        "blind": "answer 是選項字母陣列，通常一個；認為不只一個選項同樣正確才全部列出。",
        "check": "每題都要判；看不清楚附圖、無法確認詳解內容時判 reasoning_error。",
    }[role]
    return (f"## 輸出\n把全部 {n} 題的結果寫成一個 JSON 檔（UTF-8）：`{out_path.resolve()}`\n"
            f"內容只有這個物件、不要加說明文字：\n"
            f'{{"batch": "{name}", "results": [{sample}]}}\n'
            f"每一題都要有一筆，id 照上面的題目 id。{extra}\n")


def role_header(role: str, name: str) -> str:
    system, _ = X.ROLES[role]
    label = {"writer": "寫手", "blind": "盲解", "check": "比對"}[role]
    # 提示詞裡提到的工具名改成「寫進結果檔」
    for tool in (X.WRITER_TOOL, X.BLIND_TOOL, X.CHECK_TOOL):
        system = system.replace(f"用 {tool} 工具回傳", "寫進結果檔").replace(f"（用 {tool} 工具回傳）", "")
    rule = ("只讀這份批次檔與它提到的附圖，不要去讀題庫 JSON、答案或別的批次檔。" if role == "blind"
            else "只讀這份批次檔與它提到的附圖。")
    return f"# 批次 {name}（{label}）\n\n## 任務說明\n{system}\n\n{rule}\n\n"


def strip_tool_line(text: str) -> str:
    """去掉提示詞最後一行「請用 … 工具回傳…」（子代理改寫結果檔）。"""
    lines = text.rstrip("\n").split("\n")
    if lines and "工具回傳" in lines[-1]:
        lines = lines[:-1]
    return "\n".join(lines).rstrip("\n")


def write_batch(out_dir: Path, role: str, name: str, items: list[tuple[dict, str]]) -> Path:
    """items = [(題目, 題目這段的提示文字)]。"""
    body = [role_header(role, name), "## 題目\n"]
    for q, text in items:
        body.append(figure_line(q, out_dir) + strip_tool_line(text) + "\n")
    body.append(closing(role, name, len(items), result_path(out_dir, role, name)))
    p = out_dir / role / f"{name}.md"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text("\n".join(body), "utf-8")
    return p


# ---------------------------------------------------------------- 匯出

def load_manifest(out_dir: Path) -> dict:
    p = out_dir / "manifest.json"
    return json.loads(p.read_text("utf-8")) if p.exists() else {"batches": {}, "hashes": {}}


def save_manifest(out_dir: Path, manifest: dict) -> None:
    (out_dir / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=1), "utf-8")


def export(out_dir: Path, todo: list[tuple[str, str, dict]], batch_size: int) -> None:
    """寫手與盲解兩種批次檔；同一題在兩種批次裡同名同序。"""
    out_dir.mkdir(parents=True, exist_ok=True)
    idx = X.QuestionIndex()
    manifest = load_manifest(out_dir)
    by_paper: dict[tuple[str, str], list[dict]] = {}
    for exam_dir, subject, q in todo:
        by_paper.setdefault((exam_dir, subject), []).append(q)
    total = 0
    for (exam_dir, subject), qs in by_paper.items():
        for k, start in enumerate(range(0, len(qs), batch_size), 1):
            chunk = qs[start:start + batch_size]
            name = chunk_name(exam_dir, subject, k)
            writer_items = [(q, X.writer_prompt(q, idx.group_prev(q), idx.topic_name(q))) for q in chunk]
            blind_items = [(q, X.blind_prompt(q, idx.group_prev(q))) for q in chunk]
            write_batch(out_dir, "writer", name, writer_items)
            write_batch(out_dir, "blind", name, blind_items)
            manifest["batches"][name] = {"exam": exam_dir, "subject": subject, "ids": [q["id"] for q in chunk]}
            for q in chunk:
                manifest["hashes"][q["id"]] = X.answers_hash(q)
            total += len(chunk)
            print(f"{name}: {len(chunk)} 題")
    save_manifest(out_dir, manifest)
    print(f"共 {total} 題、{len(manifest['batches'])} 批；寫手批次在 {out_dir / 'writer'}、盲解批次在 {out_dir / 'blind'}")


# ---------------------------------------------------------------- 讀結果

def read_results(out_dir: Path, role: str, name: str, expected: list[str],
                 problems: list[str], errors: dict[str, list[str]]) -> dict[str, dict]:
    """讀一份結果檔 → {id: 整理後的結果}。批次層級的問題進 problems，單題的進 errors[id]；有問題的題不回傳。
    結果檔還不存在（那個角色還沒跑）→ 回傳空的、不算問題。"""
    p = result_path(out_dir, role, name)
    if not p.exists():
        return {}
    try:
        payload = json.loads(p.read_text("utf-8"))
        rows = payload["results"]
        assert isinstance(rows, list)
    except (ValueError, KeyError, TypeError, AssertionError) as e:
        problems.append(f"{role}/{name}: 結果檔格式錯誤（{e}）")
        return {}
    out: dict[str, dict] = {}
    want, seen = set(expected), set()
    for r in rows:
        qid = str(r.get("id", "")) if isinstance(r, dict) else ""
        if qid not in want:
            problems.append(f"{role}/{name}: 多出不在批次裡的 id {qid!r}")
            continue
        if qid in seen:
            errors.setdefault(qid, []).append(f"{role}: 結果重複")
            continue
        seen.add(qid)
        tool_input = dict(r)
        tool_input.pop("id", None)
        if role == "writer" and isinstance(tool_input.get("why_others"), dict):
            tool_input["why_others"] = [{"option": k, "reason": v} for k, v in tool_input["why_others"].items()]
        parsed, err = X.parse_tool_output(role, tool_input)
        if err:
            errors.setdefault(qid, []).append(f"{role}: {err}")
            continue
        out[qid] = parsed
    for qid in sorted(want - seen):
        errors.setdefault(qid, []).append(f"{role}: 結果檔 {name} 缺這題")
    return out


def collect(out_dir: Path, roles=ROLE_DIRS) -> tuple[dict, dict[str, dict[str, dict]], list[str], dict[str, list[str]]]:
    manifest = load_manifest(out_dir)
    results: dict[str, dict[str, dict]] = {r: {} for r in ROLE_DIRS}
    problems: list[str] = []
    errors: dict[str, list[str]] = {}
    for name, m in manifest["batches"].items():
        for role in roles:
            results[role].update(read_results(out_dir, role, name, m["ids"], problems, errors))
    return manifest, results, problems, errors


# ---------------------------------------------------------------- 比對批次

def export_check(out_dir: Path) -> None:
    """寫手格式合格、盲解有結果的題才送比對（格式不合格的直接不通過，同 explain）。"""
    idx = X.QuestionIndex()
    manifest, results, problems, errors = collect(out_dir, roles=("writer", "blind"))
    n_batches = total = skipped = 0
    for name, m in manifest["batches"].items():
        items = []
        for qid in m["ids"]:
            q = idx.by_id.get(qid)
            w, b = results["writer"].get(qid), results["blind"].get(qid)
            if q is None or w is None or b is None:
                continue
            if X.format_problems(q, w):
                skipped += 1
                continue
            items.append((q, X.check_prompt(q, idx.group_prev(q), w, b)))
        if items:
            write_batch(out_dir, "check", name, items)
            n_batches += 1
            total += len(items)
    for line in problems + [f"{qid}: {'；'.join(e)}" for qid, e in sorted(errors.items())]:
        print(f"[warn] {line}", file=sys.stderr)
    print(f"比對批次 {n_batches} 份、{total} 題（寫手格式不合格、直接不通過 {skipped} 題）；在 {out_dir / 'check'}")


# ---------------------------------------------------------------- 匯入

def import_results(out_dir: Path, model: str) -> dict[str, dict]:
    idx = X.QuestionIndex()
    manifest, results, problems, errors = collect(out_dir)
    # 只算已經有任何結果（或結果有問題）的題；還沒跑的批次不算「未完成」
    touched = set(errors) | {qid for role in ROLE_DIRS for qid in results[role]}
    ids = [qid for m in manifest["batches"].values() for qid in m["ids"] if qid in touched]
    for line in problems:
        print(f"[warn] {line}", file=sys.stderr)
    state = {"ids": ids, "hashes": manifest["hashes"], "results": results, "errors": errors, "model": model, "usage": {}}
    written = X.write_entries(state, idx)
    X.summarize(state, written)
    return written


# ---------------------------------------------------------------- CLI

def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--export", metavar="DIR", help="產生寫手與盲解批次檔")
    g.add_argument("--export-check", metavar="DIR", help="寫手、盲解結果都有了之後，產生比對批次檔")
    g.add_argument("--import", dest="import_dir", metavar="DIR", help="匯入三種結果，寫進 data/explanations")
    ap.add_argument("--only", help="只做某份試卷，例如 110-1/NEURO")
    ap.add_argument("--limit", type=int, help="最多幾題")
    ap.add_argument("--years", help="只做這幾年（民國年），例如 110-112 或 110,112")
    ap.add_argument("--force", action="store_true", help="已有詳解的也重做（人工寫的除外）")
    ap.add_argument("--batch-size", type=int, default=40, help="每批最多幾題（預設 40）")
    ap.add_argument("--model", default=DEFAULT_MODEL, help="寫進詳解的 model 標示")
    args = ap.parse_args(argv)

    if args.export:
        from .fetch import parse_years
        years = parse_years(args.years) if args.years else None
        todo = X.select_todo(args.only, args.limit, args.force, years)
        if not todo:
            print("沒有要生成的題目。")
            return
        export(Path(args.export), todo, args.batch_size)
    elif args.export_check:
        export_check(Path(args.export_check))
    else:
        import_results(Path(args.import_dir), args.model)


if __name__ == "__main__":
    main()
