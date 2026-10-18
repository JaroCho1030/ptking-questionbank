"""文字正規化。

考選部部分 PDF 的字型 cmap 會把常用字對到「康熙部首」區段（U+2F00–U+2FD5）或
「CJK 部首補充」（U+2E80–U+2EF3），例如 ⽤ (U+2F64) 其實是 用 (U+7528)。
這些字對 NFKC 正規化會回到正常的 CJK 統一表意文字，但我們不能整段做 NFKC，
因為 NFKC 也會把 ①②③ 變成 1 2 3、全形括號變半形，這些在題目裡是有意義的。
所以只對部首區段逐字做 NFKC。
"""
from __future__ import annotations

import re
import unicodedata

_RADICAL_RANGES = ((0x2E80, 0x2EF3), (0x2F00, 0x2FD5))

_FULLWIDTH_LETTER = {
    "Ａ": "A", "Ｂ": "B", "Ｃ": "C", "Ｄ": "D", "Ｅ": "E",
    "ａ": "A", "ｂ": "B", "ｃ": "C", "ｄ": "D", "ｅ": "E",
}


def _is_radical(ch: str) -> bool:
    cp = ord(ch)
    return any(lo <= cp <= hi for lo, hi in _RADICAL_RANGES)


def fix_radicals(text: str) -> str:
    out = []
    for ch in text:
        if _is_radical(ch):
            out.append(unicodedata.normalize("NFKC", ch))
        else:
            out.append(ch)
    return "".join(out)


# 部分 PDF（例：113-1 NEURO）的圈號字型 ToUnicode 對錯：①–⑥ 被對到西里爾字母
# U+045B–U+0460（ћ ќ ѝ ў џ Ѡ），pdfplumber 甚至直接丟掉這些字。①–⑥ 已對照 PDF 畫面確認，
# ⑦–⑩ 依同樣位移推定。考題不會出現西里爾字母，所以整段替換是安全的。
_BROKEN_CIRCLED_BASE = 0x045B
_BROKEN_CIRCLED = {_BROKEN_CIRCLED_BASE + i: chr(0x2460 + i) for i in range(10)}
_CIRCLED_RE = re.compile(r"[\u2460-\u2473]")  # ①–⑳
_CYRILLIC_RE = re.compile(r"[\u0400-\u04FF]")


def has_broken_circled(text: str) -> bool:
    return any(ord(ch) in _BROKEN_CIRCLED for ch in text)


def fix_broken_circled(text: str) -> str:
    return text.translate(_BROKEN_CIRCLED)


def count_circled(text: str) -> int:
    return len(_CIRCLED_RE.findall(text))


def has_cyrillic(text: str) -> bool:
    return bool(_CYRILLIC_RE.search(text))


def normalize_answer_letter(ch: str) -> str:
    """Ａ→A，容忍全形/半形/小寫。"""
    ch = ch.strip()
    return _FULLWIDTH_LETTER.get(ch, ch.upper())


_WS = re.compile(r"[ \t　]+")


def squash_spaces(text: str) -> str:
    """把連續空白壓成一個，並去掉 CJK 字元之間多餘的空白（新版 PDF 會在中英數之間插空白，保留那些）。"""
    text = _WS.sub(" ", text).strip()
    # CJK 與 CJK 之間的單一空白是換行接合造成的，移除
    text = re.sub(r"(?<=[一-鿿　-〿＀-￯])\s+(?=[一-鿿　-〿＀-￯])", "", text)
    return text


# 私用區（PUA）裡的字：字型自造字，對回標準字。112-1 BASIC 的「𧿹」(拇) 用 U+EEE7。
_PUA_FIXES = {"\ueee7": "拇"}
# 控制字元（例：\x81，零寬的壞字形被 pdfplumber 當成 C1 控制碼），保留換行與 tab
_CONTROL = re.compile(r"[\x00-\x08\x0b-\x1f\x7f-\x9f]")


# 英文撇號用彎引號（Crohn’s）時，中文字型（PingFang、Noto Sans TC）會把 ’ 畫成全形，看起來像「Crohn’ s」。
# 前後接英文字母的一律換成直撇號 '（題庫裡其他地方本來就用直撇號，例：Romberg's）。
_LATIN_APOS = re.compile(r"(?<=[A-Za-z])[’‘]|[’‘](?=[A-Za-z])")


def clean_text(text: str) -> str:
    text = _CONTROL.sub("", text)
    text = _LATIN_APOS.sub("'", text)
    for bad, good in _PUA_FIXES.items():
        text = text.replace(bad, good)
    return squash_spaces(fix_radicals(text))
