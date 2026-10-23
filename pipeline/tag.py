"""依 taxonomy.yaml 批次標記每題的主題（topic）、次要主題與標籤（tags）。

子代理分批（2026-10-19 起全標用這個，不需要 API key；docs/PIPELINE.md「標記流程」）：
    python -m pipeline.tag --export-batches data/tagging/full/batches --only 105-1/NEURO --limit 40 --force
        → 每份試卷一個批次檔 <年-次>_<科目>.md ＋ manifest.json；子代理讀批次檔、把結果寫成
          <批次資料夾同層>/results/<同名>.json：{"batch": "...", "results": [{id, topic, confidence, tags, alt_topic, secondary_topics}]}
    python -m pipeline.tag --import-results data/tagging/full/results --batches-dir data/tagging/full/batches
        → 檢查（主題 code 必須在分類表、次要主題 clean_secondary、缺題列出）後寫回 data/json

跨科重判（2026-10-19，跨科規則修訂後用；docs/PIPELINE.md「複數主題的規則」）：
    python -m pipeline.tag --export-rejudge data/tagging/rejudge/batches --rejudge-conf 0.5,0.6
        → 主要主題與 alt_topic 一個本科一個別科的題目，子代理只選 A（維持）或 B（改用第二候選）
    python -m pipeline.tag --import-rejudge data/tagging/rejudge/results --batches-dir data/tagging/rejudge/batches

Claude API（備用）：
    export ANTHROPIC_API_KEY=...
    python -m pipeline.tag                       # 標記所有還沒有 topic 的題目
    python -m pipeline.tag --subject NEURO --limit 40   # 先小量試跑，看 data/review/review_queue.csv 滿不滿意
    python -m pipeline.tag --force --only 113-1/ORTHO    # 重標某科
    python -m pipeline.tag --apply-overrides     # 套用 data/review/topic_overrides.csv（人工改過的答案，永遠優先）
    python -m pipeline.tag --remap OLD=NEW        # 分類表改 code 後批次改名

輸出直接寫回 data/json/*.json 的 topic / tags / topic_confidence / topic_source 欄位。
信心 < TAG_LOW_CONFIDENCE 的題目會在 validate 時進審核清單。

人工覆寫檔 data/review/topic_overrides.csv 欄位：id,topic,tags,secondary（tags、secondary 用 | 分隔，可留空；
secondary 是次要主題 code，可跨科；欄位不存在或留空 = 不動既有次要主題，填 - 表示清空）
標記過的題目 topic_source="llm"；人工覆寫後 topic_source="human"，之後 --force 也不會動它。
"""
from __future__ import annotations

import argparse
import csv
import json
import sys
import time
from pathlib import Path

import yaml

from . import config as C
from .taxonomy_doc import topic_lines

SYSTEM_PROMPT = """你是台灣物理治療師國家考試的資深出題委員與教師。
你會收到同一科目的多道選擇題，以及這一科的主題分類表（code、名稱、包含、不含、關鍵字）。「不含」列的是容易混淆、要歸到別處的內容，箭頭後面是該歸的主題。
請替每一題選出「最主要」考的一個主題 code，只能從給定清單選，先看本科主題（規則 5）。判斷依據是題目在考什麼知識點，
而不是題幹裡出現的場景（例如題目用中風病人當情境但其實在考 Berg 量表的計分，主題應是評估與量表）。
confidence 依下列刻度給（只用這幾個值附近）：
  0.95：只可能是這個主題。
  0.8：主要是這個主題，另一個主題只沾到一點。
  0.6：這個主題較核心，但另一個主題也說得通。
  0.5：兩個主題都同樣合理，選了較核心的那個。
  0.3：拿不準，可能標錯（題目資訊不足、看不到圖、或分類表裡找不到合適的主題）。
alt_topic：第二可能的主題 code（confidence 0.8 以下一定要給；0.95 可給 null）。alt_topic 是給人工審核用的備選，
不是次要主題，不可與主要主題相同。
tags 給 1 到 3 個精確的概念關鍵字（量表名、疾病名、技術名、解剖構造），中文或原文皆可，不要跟主題名稱重複。
secondary_topics（次要主題）是可選的，預設給空陣列；全部題目裡大約只有不到十分之一需要。
只有題目「真的同時考兩個知識點」才加，最多 2 個，不可與主要主題相同。規則：
1. 拿不準主要主題時，不要用次要主題當備案：照樣選較核心的那個、confidence 給低，交給人工審核。
2. 看考點，情境不算：題目考量表的內容或計分 → 只標評估主題，不加疾病；考疾病的特徵或病程 → 只標疾病主題，
   不加評估。題幹出現哪種病人只是情境（例：「中風病人的 Brunnstrom 第幾期出現痙攣」考的是分期內容，只標一個）。
3. 本科試卷單純問解剖構造或神經支配範圍（例：食指掌側感覺由哪條神經支配）→ 主要主題選本科最接近的主題，
   次要主題加基礎學的解剖主題；若問的是損傷後的表現或處置，就只標本科主題。
4. 典型的跨科例子：神經科試卷考正常步態週期的肌肉活動 → 主要選本科、次要加基礎學的步態與姿態。
5. 跨科：本科有專門涵蓋這個考點的主題就留本科；本科只有籠統或勉強相關的主題、而別科有專門的主題時選別科。
   籠統主題指概論類、「基礎與原理」、內外科概論、兒童發展這類大類，只是沾得上邊、不是專門講這個考點。
   跨科例：概論試卷考良性陣發性姿勢性眩暈 → NEURO-VESTIB；心肺小兒試卷考站姿動作控制 → NEURO-BALANCE；
   骨科試卷考 Kaltenborn 肩關節鬆動 → TECH-JOINTMOB；骨科試卷考小孩腳趾內向 → CARDPED-PED-OTHER；
   心肺試卷考洗手時機 → INTRO-SAFETY；技術學試卷考 Wernicke 失語症是哪一區受損 → BASIC-ANA-NEU。
   留本科例：心肺試卷考冠狀動脈分支 → CARDPED-CV-FOUND（本科專門講循環系統）；骨科試卷考脊椎韌帶扭傷 → ORTHO-LUMBAR；
   神經科試卷考神經解剖 → NEURO-FOUND；考周邊神經支配 → NEURO-PNI。
   跨科時主要主題直接選「其他科目主題」裡最合適的那個，信心照刻度給，不另掛本科次要主題。
清單分成「本科主題」與「其他科目主題」兩段。"""

TOOL_NAME = "tag_questions"


def load_taxonomy():
    return yaml.safe_load(C.TAXONOMY_PATH.read_text("utf-8"))


def subject_topics(tax: dict, subject_code: str) -> list[dict]:
    for s in tax["subjects"]:
        if s["code"] == subject_code:
            return s["topics"]
    raise KeyError(subject_code)


def all_topic_codes(tax: dict) -> list[str]:
    return [t["code"] for s in tax["subjects"] for t in s["topics"]]


def build_tool(topic_codes: list[str], all_codes: list[str] | None = None) -> dict:
    return {
        "name": TOOL_NAME,
        "description": "回傳每一題的主題標記",
        "input_schema": {
            "type": "object",
            "properties": {
                "results": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "id": {"type": "string"},
                            "topic": {"type": "string", "enum": all_codes or topic_codes},
                            "confidence": {"type": "number", "minimum": 0, "maximum": 1},
                            "tags": {"type": "array", "items": {"type": "string"}, "maxItems": 3},
                            "alt_topic": {"type": ["string", "null"], "enum": (all_codes or topic_codes) + [None]},
                            "secondary_topics": {"type": "array", "maxItems": 2,
                                                 "items": {"type": "string", "enum": all_codes or topic_codes}},
                        },
                        "required": ["id", "topic", "confidence", "tags"],
                    },
                }
            },
            "required": ["results"],
        },
    }


def format_batch(topics: list[dict], questions: list[dict], by_id: dict[str, dict] | None = None,
                 other_topics: list[dict] | None = None, closing: str | None = None) -> str:
    """by_id：同一份試卷的所有題目。題組（承上題）後面的題目會附上前題題幹，模型才看得懂在問什麼。"""
    by_id = by_id or {}
    lines = ["## 本科主題分類表（主要主題原則上選這些 code）"]
    for t in topics:
        grp = f"[{t['group']}] " if t.get("group") else ""
        lines.append(f"- {t['code']}：{grp}{t['name']}")
        lines += topic_lines(t)
    if other_topics:
        lines.append("\n## 其他科目的主題（次要主題可用；本科只有籠統主題時也可當主要主題）")
        for t in other_topics:
            lines.append(f"- {t['code']}：{t['name']}")
    lines.append("\n## 題目")
    for q in questions:
        ans = "/".join(q["accepted_answers"]) or ("送分" if q["is_voided"] else "?")
        lines.append(f"\n### {q['id']}")
        if (q.get("group_pos") or 1) > 1:
            prev = [x for x in by_id.values() if x.get("group_id") == q["group_id"] and x["group_pos"] < q["group_pos"]]
            for x in sorted(prev, key=lambda x: x["group_pos"]):
                lines.append(f"（題組前題 {x['id']}，僅供理解情境：{x['stem']}）")
        if q.get("figure"):
            lines.append("（本題有附圖，你看不到圖，依文字判斷）")
        lines.append(q["stem"])
        for k in C.OPTION_LETTERS:
            lines.append(f"{k}. {q['options'][k]}")
        lines.append(f"答案：{ans}")
    lines.append("\n" + (closing or f"請用 {TOOL_NAME} 工具回傳全部 {len(questions)} 題的結果。"))
    return "\n".join(lines)


def other_subject_topics(tax: dict, subject_code: str) -> list[dict]:
    return [t for s in tax["subjects"] if s["code"] != subject_code for t in s["topics"]]


def clean_secondary(r: dict, primary: str, valid: set[str]) -> list[str]:
    """次要主題：去掉不在分類表的、與主要相同的、重複的，最多 2 個。"""
    out: list[str] = []
    for c in r.get("secondary_topics") or []:
        c = str(c).strip()
        if c in valid and c != primary and c not in out:
            out.append(c)
    return out[:2]


def apply_result(q: dict, r: dict, valid_codes: set[str]) -> str | None:
    """把一筆模型結果寫進題目；API 與子代理共用。不合格回傳原因（題目不動），合格回傳 None。
    主要主題可跨科（docs/PIPELINE.md「複數主題的規則」），所以這裡只檢查 code 在分類表內。"""
    topic = str(r.get("topic") or "").strip()
    if topic not in valid_codes:
        return f"主要主題 {topic or '（空）'} 不在分類表"
    try:
        conf = min(max(float(r.get("confidence")), 0.0), 1.0)
    except (TypeError, ValueError):
        return "confidence 不是數字"
    q["topic"] = topic
    q["topic_confidence"] = round(conf, 2)
    q["tags"] = [str(t).strip() for t in (r.get("tags") or []) if str(t).strip()][:3]
    q["secondary_topics"] = clean_secondary(r, topic, valid_codes)
    alt = str(r.get("alt_topic") or "").strip()
    q["alt_topic"] = alt if alt in valid_codes and alt != topic else None
    q["topic_source"] = "llm"
    return None


def call_llm(client, topics: list[dict], questions: list[dict], by_id: dict[str, dict] | None = None,
             tax: dict | None = None, subject_code: str | None = None) -> dict[str, dict]:
    tool = build_tool([t["code"] for t in topics], all_topic_codes(tax) if tax else None)
    other = other_subject_topics(tax, subject_code) if tax and subject_code else None
    for attempt in range(3):
        try:
            resp = client.messages.create(
                model=C.TAG_MODEL,
                max_tokens=4096,
                system=SYSTEM_PROMPT,
                messages=[{"role": "user", "content": format_batch(topics, questions, by_id, other)}],
                tools=[tool],
                tool_choice={"type": "tool", "name": TOOL_NAME},
            )
            for block in resp.content:
                if block.type == "tool_use" and block.name == TOOL_NAME:
                    return {r["id"]: r for r in block.input["results"]}
            raise RuntimeError("回應裡沒有 tool_use")
        except Exception as e:  # noqa: BLE001
            wait = 5 * (attempt + 1)
            print(f"[warn] API 失敗（{e}），{wait}s 後重試", file=sys.stderr)
            time.sleep(wait)
    raise RuntimeError("連續失敗 3 次")


def iter_files(only: str | None, subject: str | None):
    for p in sorted(C.JSON_DIR.glob("*/*.json")):
        key = f"{p.parent.name}/{p.stem}"
        if only and only != key:
            continue
        if subject and p.stem != subject:
            continue
        yield p, json.loads(p.read_text("utf-8"))


def save(p: Path, data: dict) -> None:
    p.write_text(json.dumps(data, ensure_ascii=False, indent=1), "utf-8")


def select_todo(args):
    """依 --only/--subject/--limit/--force 挑出要標的題目：(檔案路徑, 資料, 科目, 待標題目)。人工覆寫的永遠跳過。"""
    budget = args.limit
    for p, data in iter_files(args.only, args.subject):
        todo = [q for q in data["questions"]
                if q.get("topic_source") != "human" and (args.force or not q.get("topic"))]
        if not todo:
            continue
        if budget is not None:
            todo = todo[:budget]
        yield p, data, data["exam"]["subject_code"], todo
        if budget is not None:
            budget -= len(todo)
            if budget <= 0:
                break


def tag(args) -> None:
    import anthropic
    client = anthropic.Anthropic()
    tax = load_taxonomy()
    valid_codes = set(all_topic_codes(tax))
    for p, data, subj, todo in select_todo(args):
        topics = subject_topics(tax, subj)
        print(f"{p.parent.name}/{p.stem}: 標記 {len(todo)} 題")
        by_id = {q["id"]: q for q in data["questions"]}
        for i in range(0, len(todo), C.TAG_BATCH_SIZE):
            batch = todo[i:i + C.TAG_BATCH_SIZE]
            if args.dry_run:
                print(format_batch(topics, batch, by_id, other_subject_topics(tax, subj))[:1500] + "\n…")
                return
            results = call_llm(client, topics, batch, by_id, tax, subj)
            for q in batch:
                r = results.get(q["id"])
                err = apply_result(q, r, valid_codes) if r else "沒有回傳結果"
                if err:
                    print(f"[warn] {q['id']} {err}", file=sys.stderr)
            save(p, data)  # 每批就存，中斷也不會白做
    print("完成。接著跑 python -m pipeline.validate 看低信心題目。")


# ---------------------------------------------------------------- 子代理分批

def batch_name(p: Path) -> str:
    return f"{p.parent.name}_{p.stem}"          # data/json/105-1/NEURO.json → 105-1_NEURO


def batch_closing(name: str, n: int) -> str:
    return (f"## 輸出\n把全部 {n} 題的結果寫成一個 JSON 檔（UTF-8），內容只有這個物件、不要加說明文字：\n"
            f'{{"batch": "{name}", "results": [{{"id": "題目 id", "topic": "主題 code（原則上本科）", "confidence": 0.0–1.0, '
            f'"tags": ["1–3 個關鍵字"], "alt_topic": "第二可能的主題 code 或 null", "secondary_topics": []}}]}}\n'
            "每一題都要有一筆；topic 先看「本科主題分類表」，本科只有籠統主題而別科有專門主題時才用其他科目的 code；"
            "secondary_topics 大多是空陣列，最多 2 個。")


def export_batches(args, out_dir: Path) -> None:
    tax = load_taxonomy()
    out_dir.mkdir(parents=True, exist_ok=True)
    manifest_path = out_dir / "manifest.json"
    manifest = json.loads(manifest_path.read_text("utf-8")) if manifest_path.exists() else {}
    total = 0
    for p, data, subj, todo in select_todo(args):
        name = batch_name(p)
        by_id = {q["id"]: q for q in data["questions"]}
        body = format_batch(subject_topics(tax, subj), todo, by_id, other_subject_topics(tax, subj),
                            closing=batch_closing(name, len(todo)))
        (out_dir / f"{name}.md").write_text(f"# 批次 {name}\n\n## 任務說明\n{SYSTEM_PROMPT}\n\n{body}\n", "utf-8")
        manifest[name] = {"json": f"{p.parent.name}/{p.stem}", "subject": subj, "ids": [q["id"] for q in todo]}
        total += len(todo)
        print(f"{name}: {len(todo)} 題")
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=1), "utf-8")
    print(f"共 {total} 題，批次檔在 {out_dir}")


REJUDGE_PROMPT = """你是台灣物理治療師國家考試的資深出題委員與教師。
每一題有兩個候選主題：A 是目前的主要主題，B 是第二候選；其中一個屬於題目所在試卷的科目（本科），另一個屬於別科。
請依下面的規則選 A 或 B 當主要主題，並給信心。
規則：""" + SYSTEM_PROMPT.split("5. 跨科：", 1)[1].split("清單分成", 1)[0].strip().replace("\n   ", "\n") + """
另外：看題目考的知識點，不看題幹情境（題目用中風病人當情境但考 Berg 量表計分 → 評估與量表）。
confidence 刻度：0.95 只可能是這個；0.8 主要是這個；0.6 較核心但另一個也說得通；0.5 兩個同樣合理；0.3 拿不準。"""

REJUDGE_BATCH_SIZE = 60


def rejudge_candidates(confidences: set[float], tax: dict) -> list[tuple[dict, str, str]]:
    """信心在 confidences、topic 與 alt_topic 剛好一個在本科的 llm 題目 → [(題目, A, B)]。"""
    subj_of = {t["code"]: s["code"] for s in tax["subjects"] for t in s["topics"]}
    out = []
    for p, data in iter_files(None, None):
        paper = data["exam"]["subject_code"]
        for q in data["questions"]:
            a, b = q.get("topic"), q.get("alt_topic")
            if (q.get("topic_source") == "llm" and a and b and q.get("topic_confidence") in confidences
                    and (subj_of.get(a) == paper) != (subj_of.get(b) == paper)):
                out.append((q, a, b))
    return out


def format_rejudge(items: list[tuple[dict, str, str]], tax: dict, name: str) -> str:
    topics = {t["code"]: (s["code"], s.get("name", s["code"]), t) for s in tax["subjects"] for t in s["topics"]}
    lines = [f"# 重判批次 {name}", "", "## 任務說明", REJUDGE_PROMPT, "", "## 題目"]
    for q, a, b in items:
        paper = q["id"].split("-")[2]
        ans = "/".join(q["accepted_answers"]) or ("送分" if q["is_voided"] else "?")
        lines += [f"\n### {q['id']}（試卷科目：{topics[a][1] if topics[a][0] == paper else topics[b][1]}）", q["stem"]]
        lines += [f"{k}. {q['options'][k]}" for k in C.OPTION_LETTERS] + [f"答案：{ans}"]
        for label, code in (("A", a), ("B", b)):
            s, sname, t = topics[code]
            lines.append(f"候選 {label}：{code}（{'本科' if s == paper else '別科：' + sname}）{t['name']}")
            lines += topic_lines(t)
    lines += ["", "## 輸出",
              f"把全部 {len(items)} 題的結果寫成一個 JSON 檔（UTF-8），內容只有這個物件、不要加說明文字：",
              f'{{"batch": "{name}", "results": [{{"id": "題目 id", "choice": "A 或 B", "confidence": 0.0–1.0}}]}}',
              "每一題都要有一筆。"]
    return "\n".join(lines)


def export_rejudge(out_dir: Path, items: list[tuple[dict, str, str]], prefix: str = "rejudge") -> None:
    tax = load_taxonomy()
    out_dir.mkdir(parents=True, exist_ok=True)
    manifest = {}
    for i in range(0, len(items), REJUDGE_BATCH_SIZE):
        chunk = items[i:i + REJUDGE_BATCH_SIZE]
        name = f"{prefix}_{i // REJUDGE_BATCH_SIZE + 1:02d}"
        (out_dir / f"{name}.md").write_text(format_rejudge(chunk, tax, name), "utf-8")
        manifest[name] = [{"id": q["id"], "A": a, "B": b} for q, a, b in chunk]
    (out_dir / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=1), "utf-8")
    print(f"共 {len(items)} 題、{len(manifest)} 批，在 {out_dir}")


def apply_rejudge(q: dict, a: str, b: str, r: dict) -> str | None:
    """選 B 就把主要主題和第二候選對調；次要主題裡若有新的主要主題就拿掉（純函式，測試用）。"""
    if q.get("topic_source") == "human":
        return "人工覆寫過，略過"
    if q.get("topic") != a or q.get("alt_topic") != b:
        return "題目的主題已經變了，略過"
    choice = str(r.get("choice") or "").strip().upper()
    if choice not in ("A", "B"):
        return f"choice 不是 A/B：{choice!r}"
    try:
        conf = round(min(max(float(r.get("confidence")), 0.0), 1.0), 2)
    except (TypeError, ValueError):
        return "confidence 不是數字"
    if choice == "B":
        q["topic"], q["alt_topic"] = b, a
        q["secondary_topics"] = [c for c in q.get("secondary_topics") or [] if c != b]
    q["topic_confidence"] = conf
    return None


def import_rejudge(results_dir: Path, batches_dir: Path) -> None:
    manifest = json.loads((batches_dir / "manifest.json").read_text("utf-8"))
    want = {e["id"]: e for entries in manifest.values() for e in entries}
    results: dict[str, dict] = {}
    for rp in sorted(results_dir.glob("*.json")):
        for r in json.loads(rp.read_text("utf-8"))["results"]:
            results[str(r["id"])] = r
    n = n_switch = 0
    problems = []
    for p, data in iter_files(None, None):
        changed = False
        for q in data["questions"]:
            e = want.get(q["id"])
            if not e:
                continue
            r = results.get(q["id"])
            err = apply_rejudge(q, e["A"], e["B"], r) if r else "沒有結果"
            if err:
                problems.append(f"{q['id']}: {err}")
                continue
            n += 1
            n_switch += q["topic"] == e["B"]
            changed = True
        if changed:
            save(p, data)
    print(f"重判 {n} 題，改用第二候選 {n_switch} 題")
    for line in problems:
        print(f"[warn] {line}", file=sys.stderr)


def import_results(results_dir: Path, batches_dir: Path) -> None:
    tax = load_taxonomy()
    valid_codes = set(all_topic_codes(tax))
    manifest = json.loads((batches_dir / "manifest.json").read_text("utf-8"))
    n_ok = n_secondary = 0
    problems: list[str] = []
    for rp in sorted(results_dir.glob("*.json")):
        name = rp.stem
        m = manifest.get(name)
        if not m:
            problems.append(f"{name}: manifest 裡沒有這個批次")
            continue
        try:
            payload = json.loads(rp.read_text("utf-8"))
            results = {str(r["id"]): r for r in payload["results"]}
        except (ValueError, KeyError, TypeError) as e:
            problems.append(f"{name}: 結果檔格式錯誤（{e}）")
            continue
        jp = C.JSON_DIR / f"{m['json']}.json"
        data = json.loads(jp.read_text("utf-8"))
        expected = set(m["ids"])
        for q in data["questions"]:
            if q["id"] not in expected:
                continue
            if q.get("topic_source") == "human":
                continue
            r = results.get(q["id"])
            err = apply_result(q, r, valid_codes) if r else "沒有結果"
            if err:
                problems.append(f"{q['id']}: {err}")
                continue
            n_ok += 1
            n_secondary += bool(q["secondary_topics"])
        extra = set(results) - expected
        if extra:
            problems.append(f"{name}: 多出不在批次裡的 id {sorted(extra)[:5]}")
        save(jp, data)
    print(f"匯入 {n_ok} 題；帶次要主題 {n_secondary} 題（{n_secondary / max(n_ok, 1):.0%}）")
    for line in problems:
        print(f"[warn] {line}", file=sys.stderr)
    if problems:
        print(f"有 {len(problems)} 個問題，上面列出的題目沒有寫入（重跑那份批次即可）")


def apply_overrides() -> None:
    path = C.REVIEW_DIR / "topic_overrides.csv"
    if not path.exists():
        print(f"沒有 {path}")
        return
    overrides = {}
    with path.open(encoding="utf-8-sig") as f:
        for row in csv.DictReader(f):
            if row.get("id") and row.get("topic"):
                overrides[row["id"].strip()] = row
    n = 0
    for p, data in iter_files(None, None):
        changed = False
        for q in data["questions"]:
            o = overrides.get(q["id"])
            if o:
                q["topic"] = o["topic"].strip()
                tags = [t.strip() for t in (o.get("tags") or "").split("|") if t.strip()]
                if tags:
                    q["tags"] = tags
                sec = (o.get("secondary") or "").strip()
                if sec == "-":
                    q["secondary_topics"] = []
                elif sec:
                    q["secondary_topics"] = [c.strip() for c in sec.split("|") if c.strip() and c.strip() != q["topic"]][:2]
                q["topic_confidence"] = 1.0
                q["alt_topic"] = None
                q["topic_source"] = "human"
                changed = True
                n += 1
        if changed:
            save(p, data)
    print(f"套用 {n} 筆人工覆寫。")


def remap(spec: str) -> None:
    old, new = spec.split("=", 1)
    n = 0
    for p, data in iter_files(None, None):
        changed = False
        for q in data["questions"]:
            if q.get("topic") == old:
                q["topic"] = new
                changed = True
                n += 1
            if old in (q.get("secondary_topics") or []):
                q["secondary_topics"] = [new if c == old else c for c in q["secondary_topics"]]
                changed = True
        if changed:
            save(p, data)
    print(f"{old} -> {new}：改了 {n} 題。")


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--subject", help="只標某科，例如 NEURO")
    ap.add_argument("--only", help="只標某檔，例如 108-1/NEURO")
    ap.add_argument("--limit", type=int, help="最多標幾題（試跑用）")
    ap.add_argument("--force", action="store_true", help="已標過的也重標（人工覆寫的除外）")
    ap.add_argument("--dry-run", action="store_true", help="只印出會送給模型的提示")
    ap.add_argument("--apply-overrides", action="store_true")
    ap.add_argument("--remap", help="OLD=NEW")
    ap.add_argument("--export-batches", metavar="DIR", help="產生子代理批次檔（一份試卷一檔）")
    ap.add_argument("--import-results", metavar="DIR", help="匯入子代理結果")
    ap.add_argument("--batches-dir", default=str(C.ROOT / "data" / "tagging" / "batches"),
                    help="--import-results 用的 manifest 所在資料夾")
    ap.add_argument("--export-rejudge", metavar="DIR",
                    help="重判跨科：信心在 --rejudge-conf、主要主題與 alt_topic 一個本科一個別科的題目，只選 A/B")
    ap.add_argument("--rejudge-conf", default="0.5,0.6", help="重判哪些信心值，逗號分隔")
    ap.add_argument("--import-rejudge", metavar="DIR", help="匯入重判結果（manifest 用 --batches-dir）")
    args = ap.parse_args(argv)
    if args.export_rejudge:
        confs = {float(x) for x in args.rejudge_conf.split(",")}
        export_rejudge(Path(args.export_rejudge), rejudge_candidates(confs, load_taxonomy()))
    elif args.import_rejudge:
        import_rejudge(Path(args.import_rejudge), Path(args.batches_dir))
    elif args.export_batches:
        export_batches(args, Path(args.export_batches))
    elif args.import_results:
        import_results(Path(args.import_results), Path(args.batches_dir))
    elif args.apply_overrides:
        apply_overrides()
    elif args.remap:
        remap(args.remap)
    else:
        tag(args)


if __name__ == "__main__":
    main()
