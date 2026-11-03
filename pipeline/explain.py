"""生成並審稿每題的 AI 詳解（規則見 docs/PIPELINE.md「詳解生成」）。

用法（需 ANTHROPIC_API_KEY）：
    python -m pipeline.explain --only 114-2/NEURO --dry-run   # 只印題數與第一題的三個提示詞，不打 API
    python -m pipeline.explain --only 114-2/NEURO             # 一份試卷試跑
    python -m pipeline.explain --limit 200                     # 最多送 200 題
    python -m pipeline.explain --years 113-115                 # 只做這幾年（格式同 fetch：113-115、110,112）
    python -m pipeline.explain --force --only 114-2/NEURO      # 已有詳解的也重跑（人工寫的除外）

每題三個呼叫，都走 Message Batches API：
    第一批：寫手（題目＋答案 → 三段詳解）與盲解（只給題目 → 答案與信心），兩者互不依賴
    第二批：比對（題目＋承認答案＋盲解＋詳解 → pass／reasoning_error／answer_disputed）
通過＝盲解答案 ⊆ accepted_answers（送分題不比答案）且 verdict = pass；其餘 rejected。

中途斷掉（Ctrl-C、電腦睡著）再執行同一個指令就會續跑：批次 id 與中間結果存在
data/explanations/.batches/state.json（不進 git），跑完改名成 run-<時間>.json 留底。
"""
from __future__ import annotations

import argparse
import base64
import hashlib
import html
import json
import os
import re
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import yaml

from . import config as C

WRITER_TOOL = "write_explanation"
BLIND_TOOL = "submit_answer"
CHECK_TOOL = "submit_review"
VERDICTS = ("pass", "reasoning_error", "answer_disputed")
STATUSES = ("pass", "rejected", "pending")

WRITER_SYSTEM = f"""你是物理治療系的資深教師，替準備台灣物理治療師國家考試的考生寫選擇題詳解。
你會拿到一道題目（可能附圖、可能是題組）、這題承認的正確答案與主題。依給定的答案寫詳解，不要質疑答案；答案本身有沒有問題由另外的審稿者判斷。

格式（用 {WRITER_TOOL} 工具回傳）：
- why_correct：為什麼是這個答案，2–3 句，最多 {C.EXPLAIN_MAX_CORRECT} 字（多個承認答案的題最多 {C.EXPLAIN_MAX_CORRECT_MULTI} 字）。
- why_others：每個「非正解」選項各一句為什麼不對，每項最多 {C.EXPLAIN_MAX_OTHER} 字。正解選項不要寫，非正解選項一個都不能漏。
- takeaway：這題的考點或記法，一句，最多 {C.EXPLAIN_MAX_TAKEAWAY} 字。

寫法：
- 繁體中文（台灣用語）。專有名詞第一次出現時附英文，例：小洞中風（lacunar stroke）。
- 不引用書名、頁碼、指引、年份等出處；不寫「AI」「我」「考選部指出」這類字眼。直接講道理。
- 字數的算法：中文字與標點各算 1 字，一個英文單字或一串數字算 1 字。寧可短，不要超過上限。
- 只寫有把握的內容：量表第幾項、清單有幾項、天數或年齡範圍這類精確細節，沒把握就不寫，改用不帶數字的說法；考點不要列不完整的清單（例如寫「十原則」卻只列九項）。

特殊題：
- 送分題（題目標示「一律給分」）：why_correct 開頭寫「考選部公告一律給分。」，接著說明這題的爭議點（例如題意不清、不只一個選項說得通、沒有正確選項）；why_others 可以是空陣列。
- 多個承認答案：why_correct 對每個承認的答案各說一次為什麼成立；why_others 只寫未承認的選項。
- 答案被更正（原公布答案和承認答案不同）：以承認答案為準，可以加一句說明原公布答案為什麼不對。
- 題組：會附上同組前面各題的題幹當背景，只替「本題」寫詳解。
- 附圖：圖跟題目一起給你，詳解可以直接描述圖上看到的內容。選項是圖片的題目，選項文字是空的，附圖由上而下依序是 A、B、C、D。"""

BLIND_SYSTEM = f"""你是物理治療系的資深教師，正在作答台灣物理治療師國家考試的選擇題。只依題目（與附圖）作答，用 {BLIND_TOOL} 工具回傳：
- answer：你認為正確的選項字母陣列。通常只有一個；若你認為不只一個選項同樣正確，全部列出。
- confidence：你對答案的信心，0 到 1 之間的數字。
看不清楚附圖、或題目資訊不足以判斷時，照樣選最可能的答案，但信心給低。
選項是圖片的題目，選項文字是空的，附圖由上而下依序是 A、B、C、D。"""

CHECK_SYSTEM = f"""你是物理治療師國家考試詳解的審稿者，也是物理治療系的資深教師。
你會拿到題目（與附圖）、這題承認的正確答案、你先前沒看答案時自己作答的結果，以及另一位教師寫的三段詳解。
請逐句檢查詳解的事實與推理，用 {CHECK_TOOL} 工具回傳 verdict 與一句 reason（繁體中文）：
- pass：每一句的事實與推理都正確，考生可以放心參考。
- reasoning_error：詳解有任何事實錯誤或推理錯誤、與題目或附圖不符、把對的選項說成錯（或反之）、漏寫非正解選項，或編造出處。
- answer_disputed：你認為承認答案本身在專業上站不住（另一個選項才對、不只一個選項對、或沒有正確選項）。只有答案本身有問題才用；答案沒問題、只是詳解寫錯，用 reasoning_error。
文筆、用詞、詳略不算錯，不影響判定。看不清楚附圖、無法確認詳解內容時判 reasoning_error。
送分題不判答案對錯，只看詳解對爭議的說明是否合理。
選項是圖片的題目，選項文字是空的，附圖由上而下依序是 A、B、C、D。"""


def _letters_schema(desc: str) -> dict:
    return {"type": "array", "description": desc, "items": {"type": "string", "enum": list(C.OPTION_LETTERS)}}


# strict 模式：schema 不放 minimum／maxItems 這類限制，範圍在 apply 階段檢查
WRITER_TOOL_DEF = {
    "name": WRITER_TOOL,
    "description": "回傳這一題的三段詳解",
    "strict": True,
    "input_schema": {
        "type": "object",
        "properties": {
            "why_correct": {"type": "string", "description": f"為什麼是這個答案，2–3 句，≤ {C.EXPLAIN_MAX_CORRECT} 字（多正解 ≤ {C.EXPLAIN_MAX_CORRECT_MULTI} 字）"},
            "why_others": {
                "type": "array",
                "description": f"每個非正解選項一筆，各一句為什麼不對，≤ {C.EXPLAIN_MAX_OTHER} 字",
                "items": {
                    "type": "object",
                    "properties": {
                        "option": {"type": "string", "enum": list(C.OPTION_LETTERS)},
                        "reason": {"type": "string"},
                    },
                    "required": ["option", "reason"],
                    "additionalProperties": False,
                },
            },
            "takeaway": {"type": "string", "description": f"考點或記法一句，≤ {C.EXPLAIN_MAX_TAKEAWAY} 字"},
        },
        "required": ["why_correct", "why_others", "takeaway"],
        "additionalProperties": False,
    },
}

BLIND_TOOL_DEF = {
    "name": BLIND_TOOL,
    "description": "回傳你的答案與信心",
    "strict": True,
    "input_schema": {
        "type": "object",
        "properties": {
            "answer": _letters_schema("你認為正確的選項，至少一個"),
            "confidence": {"type": "number", "description": "信心，0 到 1"},
        },
        "required": ["answer", "confidence"],
        "additionalProperties": False,
    },
}

CHECK_TOOL_DEF = {
    "name": CHECK_TOOL,
    "description": "回傳審稿結果",
    "strict": True,
    "input_schema": {
        "type": "object",
        "properties": {
            "verdict": {"type": "string", "enum": list(VERDICTS)},
            "reason": {"type": "string", "description": "一句理由"},
        },
        "required": ["verdict", "reason"],
        "additionalProperties": False,
    },
}

ROLES = {
    "writer": (WRITER_SYSTEM, WRITER_TOOL_DEF),
    "blind": (BLIND_SYSTEM, BLIND_TOOL_DEF),
    "check": (CHECK_SYSTEM, CHECK_TOOL_DEF),
}


# ---------------------------------------------------------------- 共用（validate、build 也用）

def answers_hash(q: dict) -> str:
    """accepted_answers＋is_voided 的 sha256 前 12 碼；答案被更正時會變，詳解就要重寫。"""
    key = ",".join(sorted(q.get("accepted_answers") or [])) + "|" + ("1" if q.get("is_voided") else "0")
    return hashlib.sha256(key.encode("utf-8")).hexdigest()[:12]


_ASCII_RUN = re.compile(r"[A-Za-z0-9]+(?:[.'’\-][A-Za-z0-9]+)*")


def text_len(s: str) -> int:
    """詳解字數：中文字與標點各算 1，一個英文單字或一串數字算 1，空白不算。"""
    return len(re.sub(r"\s+", "", _ASCII_RUN.sub("W", s or "")))


def hard_limit(limit: int) -> int:
    """格式檢查真正擋的字數：上限再容許 EXPLAIN_LENGTH_TOLERANCE（120 → 144、40 → 48）。"""
    return int(limit * (1 + C.EXPLAIN_LENGTH_TOLERANCE) + 1e-9)


def correct_limit(q: dict) -> int:
    return C.EXPLAIN_MAX_CORRECT_MULTI if len(q.get("accepted_answers") or []) > 1 else C.EXPLAIN_MAX_CORRECT


def format_problems(q: dict, e: dict) -> list[str]:
    """檢查一則詳解的欄位與字數（explain 的 apply 與 validate 共用）。回傳問題清單，空 = 合格。
    字數超過上限但在容許範圍（EXPLAIN_LENGTH_TOLERANCE）內算合格。"""
    probs = []
    wc, wo, tk = e.get("why_correct"), e.get("why_others"), e.get("takeaway")
    max_wc, max_tk, max_wo = hard_limit(correct_limit(q)), hard_limit(C.EXPLAIN_MAX_TAKEAWAY), hard_limit(C.EXPLAIN_MAX_OTHER)
    if not isinstance(wc, str) or not wc.strip():
        probs.append("why_correct 空白")
    elif text_len(wc) > max_wc:
        probs.append(f"why_correct {text_len(wc)} 字 > {max_wc}")
    if not isinstance(tk, str) or not tk.strip():
        probs.append("takeaway 空白")
    elif text_len(tk) > max_tk:
        probs.append(f"takeaway {text_len(tk)} 字 > {max_tk}")
    if not isinstance(wo, dict):
        probs.append("why_others 不是物件")
        return probs
    accepted = set(q.get("accepted_answers") or [])
    for k, v in wo.items():
        if k not in C.OPTION_LETTERS:
            probs.append(f"why_others 有不存在的選項 {k}")
        elif k in accepted:
            probs.append(f"why_others 含正解選項 {k}")
        elif not isinstance(v, str) or not v.strip():
            probs.append(f"why_others {k} 空白")
        elif text_len(v) > max_wo:
            probs.append(f"why_others {k} {text_len(v)} 字 > {max_wo}")
    if not q.get("is_voided"):
        missing = [k for k in C.OPTION_LETTERS if k not in accepted and k not in wo]
        if missing:
            probs.append(f"why_others 缺選項 {''.join(missing)}")
    return probs


def explanation_path(exam_dir: str, subject: str) -> Path:
    return C.EXPLANATION_DIR / exam_dir / f"{subject}.json"


def load_explanations(exam_dir: str, subject: str) -> dict[str, dict]:
    p = explanation_path(exam_dir, subject)
    if not p.exists():
        return {}
    return {e["id"]: e for e in json.loads(p.read_text("utf-8"))["explanations"]}


def save_explanations(exam_dir: str, subject: str, entries: dict[str, dict]) -> None:
    p = explanation_path(exam_dir, subject)
    p.parent.mkdir(parents=True, exist_ok=True)
    payload = {"exam_id": f"{exam_dir}-{subject}", "explanations": [entries[k] for k in sorted(entries)]}
    p.write_text(json.dumps(payload, ensure_ascii=False, indent=1) + "\n", "utf-8")


def iter_explanation_files():
    """data/explanations/{年}-{次}/{科目}.json → (年-次, 科目, {id: entry})。"""
    for p in sorted(C.EXPLANATION_DIR.glob("*/*.json")):
        if p.parent.name.startswith("."):      # .batches/ 的中間檔（run-*.json）不是詳解
            continue
        yield p.parent.name, p.stem, {e["id"]: e for e in json.loads(p.read_text("utf-8"))["explanations"]}


# ---------------------------------------------------------------- 挑題

def needs_explain(q: dict, e: dict | None, force: bool) -> bool:
    """人工寫的一律不動；其他：沒有詳解、pending、答案被更正（hash 不符）、--force，
    或只因格式不通過、但照現在的字數規則已合格（規則放寬後沒審過，要重跑）。"""
    if e and e.get("source") == "human":
        return False
    if e and e.get("status") == "rejected" and e.get("review", {}).get("fails") == ["format"] and not format_problems(q, e):
        return True
    return e is None or force or e.get("status") == "pending" or e.get("answers_hash") != answers_hash(q)


def select_todo(only: str | None = None, limit: int | None = None, force: bool = False,
                years: list[int] | None = None) -> list[tuple[str, str, dict]]:
    """回傳 [(年-次, 科目, 題目)]；年 < EXPLAIN_MIN_YEAR 的不做；years 給了就只做那幾年。"""
    out = []
    for p in sorted(C.JSON_DIR.glob("*/*.json")):
        exam_dir, subject = p.parent.name, p.stem
        if only and only != f"{exam_dir}/{subject}":
            continue
        year = int(exam_dir.split("-")[0])
        if year < C.EXPLAIN_MIN_YEAR or (years and year not in years):
            continue
        data = json.loads(p.read_text("utf-8"))
        existing = load_explanations(exam_dir, subject)
        for q in data["questions"]:
            if needs_explain(q, existing.get(q["id"]), force):
                out.append((exam_dir, subject, q))
                if limit is not None and len(out) >= limit:
                    return out
    return out


# ---------------------------------------------------------------- 提示詞

def option_images(q: dict) -> bool:
    """選項是圖片：四個選項文字都空、有附圖。"""
    return bool(q.get("figure")) and not any((q["options"].get(k) or "").strip() for k in C.OPTION_LETTERS)


def question_text(q: dict, group_prev: list[dict]) -> str:
    lines = [f"## 題目 {q['id']}"]
    if group_prev:
        lines.append(f"（本題是題組的第 {q['group_pos']} 題。同組前面的題目只當背景：）")
        for x in group_prev:
            lines.append(f"- 第 {x['number']} 題：{x['stem']}")
        lines.append("")
    if q.get("figure"):
        lines.append("（本題附圖如上）")
    lines.append(q["stem"])
    for k in C.OPTION_LETTERS:
        lines.append(f"{k}. {q['options'][k]}")
    if option_images(q):
        lines.append("（選項是圖片：選項文字為空，見附圖，由上而下依序是 A、B、C、D）")
    return "\n".join(lines)


def answer_text(q: dict) -> str:
    if q["is_voided"]:
        s = "本題考選部公告一律給分（送分）"
        if q.get("official_answer"):
            s += f"，原公布答案 {q['official_answer']}"
    else:
        s = "承認答案：" + "、".join(q["accepted_answers"])
        if len(q["accepted_answers"]) > 1:
            s += "（以上都給分）"
        if q.get("official_answer") and [q["official_answer"]] != q["accepted_answers"]:
            s += f"；原公布答案 {q['official_answer']}"
    if q.get("correction_note"):
        s += f"\n更正備註：{q['correction_note']}"
    return s


def writer_prompt(q: dict, group_prev: list[dict], topic_name: str | None) -> str:
    parts = [question_text(q, group_prev), "", answer_text(q)]
    if topic_name:
        parts.append(f"主題：{topic_name}")
    parts += ["", f"請用 {WRITER_TOOL} 工具回傳這一題的詳解。"]
    return "\n".join(parts)


def blind_prompt(q: dict, group_prev: list[dict]) -> str:
    return question_text(q, group_prev) + f"\n\n請用 {BLIND_TOOL} 工具回傳你的答案。"


def format_explanation(w: dict) -> str:
    lines = [f"why_correct：{w['why_correct']}", "why_others："]
    lines += [f"  {k}：{v}" for k, v in sorted(w["why_others"].items())] or ["  （無）"]
    lines.append(f"takeaway：{w['takeaway']}")
    return "\n".join(lines)


def check_prompt(q: dict, group_prev: list[dict], writer: dict, blind: dict) -> str:
    return "\n".join([
        question_text(q, group_prev), "", answer_text(q), "",
        f"你先前沒看答案時的作答：{'、'.join(blind['answer'])}（信心 {blind['confidence']:.2f}）", "",
        "## 待審的詳解", format_explanation(writer), "",
        f"請用 {CHECK_TOOL} 工具回傳審稿結果。",
    ])


def image_block(q: dict) -> dict | None:
    if not q.get("figure"):
        return None
    fig = (C.DATA_DIR / q["figure"]).resolve()
    if not fig.is_relative_to(C.FIGURE_DIR.resolve()) or not fig.exists():
        return None
    return {"type": "image", "source": {"type": "base64", "media_type": "image/webp",
                                        "data": base64.standard_b64encode(fig.read_bytes()).decode("ascii")}}


def request_params(role: str, q: dict, text: str) -> dict:
    system, tool = ROLES[role]
    content = []
    img = image_block(q)
    if img:
        content.append(img)
    content.append({"type": "text", "text": text})
    # Opus 5.5 不接受強制 tool_choice（any／tool 會 400）：用 auto＋strict，提示詞指定工具；沒呼叫工具當失敗
    return {
        "model": C.EXPLAIN_MODEL,
        "max_tokens": C.EXPLAIN_MAX_TOKENS,
        "system": system,
        "messages": [{"role": "user", "content": content}],
        "tools": [tool],
        "tool_choice": {"type": "auto"},
        "output_config": {"effort": C.EXPLAIN_EFFORT},
    }


# ---------------------------------------------------------------- 結果處理（純函式）

def _clean(s) -> str:
    """去頭尾空白；模型偶爾把 & 寫成 &amp; 之類的 HTML 跳脫字元，轉回原字。"""
    return html.unescape(str(s)).strip()


def parse_tool_output(role: str, tool_input: dict) -> tuple[dict | None, str | None]:
    """把工具輸入整理成存檔格式；不合格回傳 (None, 原因)。"""
    try:
        if role == "writer":
            wo = {}
            for item in tool_input["why_others"]:
                k = str(item["option"]).strip().upper()
                if k in wo:
                    return None, f"why_others 重複選項 {k}"
                wo[k] = _clean(item["reason"])
            return {"why_correct": _clean(tool_input["why_correct"]), "why_others": wo,
                    "takeaway": _clean(tool_input["takeaway"])}, None
        if role == "blind":
            ans = sorted({str(a).strip().upper() for a in tool_input["answer"]})
            if not ans or any(a not in C.OPTION_LETTERS for a in ans):
                return None, f"盲解答案異常 {tool_input['answer']!r}"
            conf = round(min(max(float(tool_input["confidence"]), 0.0), 1.0), 2)
            return {"answer": ans, "confidence": conf}, None
        if role == "check":
            verdict = str(tool_input["verdict"]).strip()
            if verdict not in VERDICTS:
                return None, f"verdict 異常 {verdict!r}"
            return {"verdict": verdict, "reason": _clean(tool_input["reason"])}, None
    except (KeyError, TypeError, ValueError) as e:
        return None, f"工具輸入格式錯誤（{e}）"
    raise KeyError(role)


def judge(q: dict, writer: dict | None, blind: dict | None, check: dict | None) -> tuple[str, list[str]]:
    """通過判定：(status, 不通過原因)。原因代碼：format／blind_mismatch／reasoning_error／answer_disputed／missing。"""
    if writer is None or blind is None:
        return "pending", ["missing"]
    fails = []
    if format_problems(q, writer):
        fails.append("format")
    if not q["is_voided"] and not set(blind["answer"]) <= set(q["accepted_answers"]):
        fails.append("blind_mismatch")
    if check is None:
        if not fails:
            return "pending", ["missing"]
    elif check["verdict"] != "pass":
        fails.append(check["verdict"])
    return ("rejected", fails) if fails else ("pass", [])


def make_entry(q: dict, writer: dict | None, blind: dict | None, check: dict | None, model: str,
               errors: list[str] | None = None) -> dict | None:
    """組成存檔的一筆。寫手沒產出就回傳 None（不寫檔，下次再跑）。"""
    if writer is None:
        return None
    status, fails = judge(q, writer, blind, check)
    review = {
        "blind_answer": blind["answer"] if blind else None,
        "blind_confidence": blind["confidence"] if blind else None,
        "verdict": check["verdict"] if check else None,
        "reason": check["reason"] if check else None,
    }
    if fails and fails != ["missing"]:
        review["fails"] = fails
    probs = format_problems(q, writer) + list(errors or [])
    if probs:
        review["problems"] = probs
    return {
        "id": q["id"], "status": status, "source": "llm", "model": model,
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "answers_hash": answers_hash(q),
        "why_correct": writer["why_correct"], "why_others": writer["why_others"], "takeaway": writer["takeaway"],
        "review": review,
    }


# ---------------------------------------------------------------- 批次與續跑

def state_dir() -> Path:
    return C.EXPLANATION_DIR / ".batches"


def load_state() -> dict | None:
    p = state_dir() / "state.json"
    return json.loads(p.read_text("utf-8")) if p.exists() else None


def save_state(state: dict) -> None:
    d = state_dir()
    d.mkdir(parents=True, exist_ok=True)
    tmp = d / "state.json.tmp"
    tmp.write_text(json.dumps(state, ensure_ascii=False, indent=1), "utf-8")
    tmp.replace(d / "state.json")


def archive_state(state: dict) -> Path:
    d = state_dir()
    dest = d / f"run-{state['run_id']}.json"
    dest.write_text(json.dumps(state, ensure_ascii=False, indent=1), "utf-8")
    (d / "state.json").unlink(missing_ok=True)
    return dest


class QuestionIndex:
    """依 id 找題目、題組前題與主題名稱（續跑時從 data/json 重新讀）。"""

    def __init__(self):
        self.by_id: dict[str, dict] = {}
        for p in sorted(C.JSON_DIR.glob("*/*.json")):
            for q in json.loads(p.read_text("utf-8"))["questions"]:
                self.by_id[q["id"]] = q
        tax = yaml.safe_load(C.TAXONOMY_PATH.read_text("utf-8"))
        self.topic_names = {t["code"]: t["name"] for s in tax["subjects"] for t in s["topics"]}

    def group_prev(self, q: dict) -> list[dict]:
        if (q.get("group_pos") or 1) <= 1:
            return []
        members = [x for x in self.by_id.values() if x.get("group_id") == q["group_id"] and x["group_pos"] < q["group_pos"]]
        return sorted(members, key=lambda x: x["group_pos"])

    def topic_name(self, q: dict) -> str | None:
        return self.topic_names.get(q.get("topic") or "")


def custom_id(role: str, qid: str) -> str:
    return f"{role}_{qid}"           # custom_id 只能 [A-Za-z0-9_-]，題目 id 剛好符合


def build_requests(role: str, qids: list[str], idx: QuestionIndex, state: dict) -> list[dict]:
    reqs = []
    for qid in qids:
        q = idx.by_id[qid]
        prev = idx.group_prev(q)
        if role == "writer":
            text = writer_prompt(q, prev, idx.topic_name(q))
        elif role == "blind":
            text = blind_prompt(q, prev)
        else:
            text = check_prompt(q, prev, state["results"]["writer"][qid], state["results"]["blind"][qid])
        reqs.append({"custom_id": custom_id(role, qid), "params": request_params(role, q, text)})
    return reqs


def add_usage(state: dict, role: str, usage) -> None:
    u = state.setdefault("usage", {}).setdefault(role, {})
    for f in ("input_tokens", "output_tokens", "cache_creation_input_tokens", "cache_read_input_tokens"):
        u[f] = u.get(f, 0) + (getattr(usage, f, None) or 0)


def collect_batch(client, batch_id: str, state: dict) -> None:
    """讀批次結果寫進 state["results"]／state["errors"]。結果順序不固定，一律用 custom_id 對應。"""
    for r in client.messages.batches.results(batch_id):
        role, qid = r.custom_id.split("_", 1)
        res = r.result
        if res.type != "succeeded":
            err = getattr(getattr(res, "error", None), "type", None) or res.type
            state["errors"].setdefault(qid, []).append(f"{role}: 批次結果 {err}")
            continue
        msg = res.message
        add_usage(state, role, msg.usage)
        state["model"] = getattr(msg, "model", None) or state.get("model")
        _, tool = ROLES[role]
        block = next((b for b in msg.content if b.type == "tool_use" and b.name == tool["name"]), None)
        if block is None:
            state["errors"].setdefault(qid, []).append(f"{role}: 沒有呼叫工具（stop_reason={msg.stop_reason}）")
            continue
        out, err = parse_tool_output(role, dict(block.input))
        if err:
            state["errors"].setdefault(qid, []).append(f"{role}: {err}")
            continue
        state["results"][role][qid] = out


def wait_batch(client, batch_id: str, label: str) -> None:
    while True:
        b = client.messages.batches.retrieve(batch_id)
        if b.processing_status == "ended":
            c = b.request_counts
            print(f"{label} 完成：成功 {c.succeeded}、錯誤 {c.errored}、取消 {c.canceled}、逾時 {c.expired}")
            return
        c = b.request_counts
        print(f"{label} 處理中（剩 {c.processing}）…", flush=True)
        time.sleep(C.EXPLAIN_POLL_SEC)


def run_phase(client, state: dict, key: str, label: str, make_reqs) -> None:
    """送出（或接續）一個批次並收結果；每一步都先存 state 才往下走。"""
    if state.get(f"{key}_done"):
        return
    if not state.get(f"{key}_batch"):
        reqs = make_reqs()
        if not reqs:
            state[f"{key}_done"] = True
            save_state(state)
            return
        b = client.messages.batches.create(requests=reqs)
        state[f"{key}_batch"] = b.id
        save_state(state)
        print(f"{label} 送出 {len(reqs)} 個請求，批次 {b.id}")
    else:
        print(f"{label} 接續批次 {state[f'{key}_batch']}")
    wait_batch(client, state[f"{key}_batch"], label)
    collect_batch(client, state[f"{key}_batch"], state)
    state[f"{key}_done"] = True
    save_state(state)


def write_entries(state: dict, idx: QuestionIndex) -> dict[str, dict]:
    """把目前結果寫進 data/explanations（寫手沒產出的不動既有那筆）。回傳 {id: entry}。"""
    res = state["results"]
    by_file: dict[tuple[str, str], list[dict]] = {}
    for qid in state["ids"]:
        q = idx.by_id.get(qid)
        if q is None:
            continue
        # 送出後答案又被更正：這批結果作廢，下次重跑
        if answers_hash(q) != state["hashes"][qid]:
            state["errors"].setdefault(qid, []).append("送出後答案已更正，結果作廢")
            continue
        e = make_entry(q, res["writer"].get(qid), res["blind"].get(qid), res["check"].get(qid),
                       state.get("model") or C.EXPLAIN_MODEL, state["errors"].get(qid))
        if e:
            exam_dir = "-".join(qid.split("-")[:2])
            by_file.setdefault((exam_dir, qid.split("-")[2]), []).append(e)
    written = {}
    for (exam_dir, subject), entries in by_file.items():
        existing = load_explanations(exam_dir, subject)
        for e in entries:
            if existing.get(e["id"], {}).get("source") == "human":
                continue        # 跑的中間有人工改寫：人工優先
            existing[e["id"]] = e
            written[e["id"]] = e
        save_explanations(exam_dir, subject, existing)
    return written


def summarize(state: dict, written: dict[str, dict]) -> None:
    n = len(state["ids"])
    status = [e["status"] for e in written.values()]
    fails = [f for e in written.values() for f in e["review"].get("fails", [])]
    print(f"\n送出 {n} 題：通過 {status.count('pass')}、不通過 {status.count('rejected')}、"
          f"未完成 {n - status.count('pass') - status.count('rejected')}")
    print(f"  不通過原因：reasoning_error {fails.count('reasoning_error')}、answer_disputed {fails.count('answer_disputed')}、"
          f"盲解答錯 {fails.count('blind_mismatch')}、格式不符 {fails.count('format')}（一題可能有多個原因）")
    for qid, errs in sorted(state["errors"].items()):
        print(f"  [warn] {qid}: {'；'.join(errs)}", file=sys.stderr)
    total_in = total_out = 0
    for role in ("writer", "blind", "check"):
        u = state.get("usage", {}).get(role)
        if not u:
            continue
        total_in += u["input_tokens"] + u["cache_creation_input_tokens"] + u["cache_read_input_tokens"]
        total_out += u["output_tokens"]
        print(f"  token {role}: 輸入 {u['input_tokens']:,}（快取寫 {u['cache_creation_input_tokens']:,}、"
              f"讀 {u['cache_read_input_tokens']:,}）、輸出 {u['output_tokens']:,}")
    print(f"  token 合計：輸入 {total_in:,}、輸出 {total_out:,}（Batch API）")


def new_state(todo: list[tuple[str, str, dict]]) -> dict:
    return {
        "run_id": datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ"),
        "model": C.EXPLAIN_MODEL,
        "ids": [q["id"] for _, _, q in todo],
        "hashes": {q["id"]: answers_hash(q) for _, _, q in todo},
        "results": {"writer": {}, "blind": {}, "check": {}},
        "errors": {},
        "usage": {},
    }


def run(client, state: dict) -> dict[str, dict]:
    idx = QuestionIndex()
    ids = state["ids"]
    run_phase(client, state, "phase1", "第一批（寫手＋盲解）",
              lambda: build_requests("writer", ids, idx, state) + build_requests("blind", ids, idx, state))
    # 第二批只送寫手格式合格、盲解有結果的題；格式不合格直接不通過，不浪費比對
    res = state["results"]
    ready = [qid for qid in ids if qid in res["writer"] and qid in res["blind"]
             and qid in idx.by_id and not format_problems(idx.by_id[qid], res["writer"][qid])]
    run_phase(client, state, "phase2", "第二批（比對）", lambda: build_requests("check", ready, idx, state))
    written = write_entries(state, idx)
    summarize(state, written)
    print(f"中間紀錄 → {archive_state(state)}")
    return written


def dry_run(todo: list[tuple[str, str, dict]]) -> None:
    print(f"會送出 {len(todo)} 題（{len(todo) * 3} 個呼叫：寫手、盲解、比對各一）")
    if not todo:
        return
    idx = QuestionIndex()
    q = todo[0][2]
    prev = idx.group_prev(q)
    fig = "（附圖：" + q["figure"] + "）\n" if q.get("figure") else ""
    fake_w = {"why_correct": "（寫手的 why_correct）", "why_others": {k: "（寫手的 why_others）" for k in C.OPTION_LETTERS
                                                                  if k not in q["accepted_answers"]},
              "takeaway": "（寫手的 takeaway）"}
    fake_b = {"answer": ["?"], "confidence": 0.0}
    for role, text in (("writer", writer_prompt(q, prev, idx.topic_name(q))), ("blind", blind_prompt(q, prev)),
                       ("check", check_prompt(q, prev, fake_w, fake_b))):
        system, _ = ROLES[role]
        print(f"\n==================== {role}（{C.EXPLAIN_MODEL}，effort {C.EXPLAIN_EFFORT}）====================")
        print(f"[system]\n{system}\n\n[user]\n{fig}{text}")


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--only", help="只做某份試卷，例如 114-2/NEURO")
    ap.add_argument("--limit", type=int, help="最多送幾題")
    ap.add_argument("--years", help="只做這幾年（民國年），例如 113-115 或 110,112")
    ap.add_argument("--force", action="store_true", help="已有詳解的也重跑（人工寫的除外）")
    ap.add_argument("--dry-run", action="store_true", help="只印題數與第一題的三個提示詞，不打 API")
    args = ap.parse_args(argv)

    from .fetch import parse_years
    years = parse_years(args.years) if args.years else None
    state = load_state()
    if args.dry_run:
        if state:
            print(f"注意：有一次沒跑完的執行（{state['run_id']}，{len(state['ids'])} 題），不加 --dry-run 會先把它跑完")
        try:
            dry_run(select_todo(args.only, args.limit, args.force, years))
        except BrokenPipeError:      # 接 | head 時後面的輸出被丟掉，不算錯
            sys.stdout = open(os.devnull, "w")
        return
    import anthropic
    client = anthropic.Anthropic()
    if state:
        print(f"接續上次沒跑完的執行 {state['run_id']}（{len(state['ids'])} 題）；這次的 --only/--years/--limit/--force 不套用")
    else:
        todo = select_todo(args.only, args.limit, args.force, years)
        if not todo:
            print("沒有要生成的題目。")
            return
        state = new_state(todo)
        save_state(state)
    run(client, state)


if __name__ == "__main__":
    main()
