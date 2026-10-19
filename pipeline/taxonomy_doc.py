"""由 taxonomy/taxonomy.yaml 產生給人看的分類表 docs/TAXONOMY.md。

用法：
    python -m pipeline.taxonomy_doc            重新產生 ../docs/TAXONOMY.md
    python -m pipeline.taxonomy_doc --check    只檢查檔案是否與 yaml 同步（不同步 exit 1）

改分類一律改 taxonomy.yaml（name / definition / excludes / keywords），再跑這支；不要手改 TAXONOMY.md。
"""
from __future__ import annotations

import argparse
import re
import sys

import yaml

from . import config as C

DOC_PATH = C.ROOT.parent / "docs" / "TAXONOMY.md"

RULES = """\
## 怎麼歸類（通則）

- 每題 1 個**主要主題**，另可掛 0～2 個**次要主題**。次要主題只用在題目真的同時考兩個主題時（全題庫約 1%），不拿來表示「不確定」。
- **看考的是什麼，不看情境**：題幹用某疾病當情境，但實際考的是量表、手法或解剖，就歸量表、手法或解剖那個主題。
- **主要主題原則上選本科**（題目所在試卷的科目）。本科有合理的主題就一定留本科，例如神經科試卷考神經解剖 → NEURO-FOUND、考周邊神經支配 → NEURO-PNI。
- **本科真的放不下時才跨科**：主要主題直接選別科最合適的那個，不另掛本科次要主題（例：技術學試卷考 Wernicke 失語症是哪一區受損 → BASIC-ANA-NEU）。
- **本科試卷單純問解剖構造或神經支配範圍**（例：食指掌側感覺由哪條神經支配）：主要主題選本科最接近的主題，次要主題加基礎學的解剖主題（BASIC-ANA-*）；若問的是損傷後的表現或處置，就只標本科主題。
- 「不含」列的是容易混淆、但要歸到別處的內容；箭頭後面是該歸的主題。
- 篩選與統計規則見 [ARCHITECTURE.md 3.8](ARCHITECTURE.md)。
"""

CODE_RE = re.compile(r"\b((?:BASIC|INTRO|TECH|NEURO|ORTHO|CARDPED)-[A-Z0-9-]*[A-Z0-9])\b")


def load() -> dict:
    return yaml.safe_load(C.TAXONOMY_PATH.read_text("utf-8"))


def _link(text: str, codes: set[str]) -> str:
    return CODE_RE.sub(lambda m: f"[{m.group(1)}](#{m.group(1).lower()})" if m.group(1) in codes else m.group(1), text)


def topic_lines(t: dict) -> list[str]:
    """給 tag.py 的提示詞用：定義、不含、關鍵字各一行。"""
    lines = [f"  包含：{t['definition']}"] if t.get("definition") else []
    if t.get("excludes"):
        lines.append("  不含：" + "；".join(t["excludes"]))
    if t.get("keywords"):
        lines.append("  關鍵字：" + "、".join(t["keywords"]))
    return lines


def render(tax: dict) -> str:
    subjects = tax["subjects"]
    codes = {t["code"] for s in subjects for t in s["topics"]}
    out = [
        "# 主題分類表",
        "",
        "> 本檔由 `questionbank/taxonomy/taxonomy.yaml` 自動產生，請勿手改。"
        "要改分類請改 yaml，再執行 `python -m pipeline.taxonomy_doc`。",
        "",
        f"分類表版本 {tax.get('version', 1)}，更新於 {tax.get('updated', '')}，共 {len(codes)} 個主題。",
        "",
        RULES,
        "## 目錄",
        "",
        "| 科目 | 主題數 |",
        "|---|---|",
    ]
    for s in subjects:
        out.append(f"| [{s['full_name']}](#subject-{s['code'].lower()}) | {len(s['topics'])} |")
    for s in subjects:
        out += ["", f'<a id="subject-{s["code"].lower()}"></a>', "",
                f"## {s['name']}（{s['code']}，{len(s['topics'])} 個主題）", ""]
        groups: list[str | None] = []
        for t in s["topics"]:
            if t.get("group") not in groups:
                groups.append(t.get("group"))
        for g in groups:
            level = "####" if g else "###"
            if g:
                out += [f"### {g}", ""]
            for t in (t for t in s["topics"] if t.get("group") == g):
                out += [f'<a id="{t["code"].lower()}"></a>', "", f"{level} {t['code']}　{t['name']}", ""]
                out.append(f"- **包含**：{_link(t['definition'], codes)}")
                exc = t.get("excludes") or []
                if len(exc) == 1:
                    out.append(f"- **不含**：{_link(exc[0], codes)}")
                elif exc:
                    out.append("- **不含**：")
                    out += [f"  - {_link(e, codes)}" for e in exc]
                out.append(f"- **關鍵字**：{'、'.join(t.get('keywords', []))}")
                out.append("")
    return "\n".join(out).rstrip() + "\n"


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--check", action="store_true", help="只檢查是否同步")
    args = ap.parse_args()
    text = render(load())
    if args.check:
        same = DOC_PATH.exists() and DOC_PATH.read_text("utf-8") == text
        print("TAXONOMY.md 與 taxonomy.yaml 同步" if same else "TAXONOMY.md 過期，請執行 python -m pipeline.taxonomy_doc")
        return 0 if same else 1
    DOC_PATH.write_text(text, "utf-8")
    print(f"已寫入 {DOC_PATH}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
