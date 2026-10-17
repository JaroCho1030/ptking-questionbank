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


def clean_text(text: str) -> str:
    return squash_spaces(fix_radicals(text))
