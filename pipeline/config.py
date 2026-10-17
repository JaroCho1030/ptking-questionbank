"""題庫管線共用設定。

所有路徑以 questionbank/ 目錄為根。
"""
from __future__ import annotations

import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT / "data"
RAW_DIR = DATA_DIR / "raw"          # 考選部原始 PDF（gitignore）
JSON_DIR = DATA_DIR / "json"        # 解析後的結構化題目（進 git）
FIGURE_DIR = DATA_DIR / "figures"   # 附圖 WebP（進 git）
FIGURE_ZOOM = 2.0                   # 裁圖解析度：PDF 1pt → 2px，全寬圖約 1100px（接近 iPhone @3x 全寬）
FIGURE_WEBP_QUALITY = 85            # 2026-10 實測：113 年 29 張 6.6 MB(PNG) → 0.6 MB，表格字與 X 光細節目視無差
REVIEW_DIR = DATA_DIR / "review"    # 待人工檢查清單（進 git）
DIST_DIR = ROOT / "dist"            # build 產物（gitignore）
TAXONOMY_PATH = ROOT / "taxonomy" / "taxonomy.yaml"

# 考選部考畢試題查詢平臺
MOEX_BASE = "https://wwwq.moex.gov.tw/exam/"
SEARCH_URL = MOEX_BASE + "wFrmExamQandASearch.aspx"
FILE_URL = MOEX_BASE + "wHandExamQandA_File.ashx"
# HTTP header 只能用 latin-1，這裡不可放中文
USER_AGENT = "ptking-questionbank/0.1 (+https://github.com/JaroCho1030/ptking-questionbank; personal exam-practice app, low-frequency access)"
REQUEST_DELAY_SEC = 1.0  # 對考選部溫柔一點

# 類科名稱（結果頁的群組標題會長得像「專技高考_物理治療師」）
CLASS_NAME_PATTERN = "物理治療師"

# 科目：考選部科目名稱（去掉括號說明後的前綴）→ 科目代碼。
# 題目 ID 用代碼：{民國年}-{次}-{代碼}-{題號三位}，例如 108-1-NEURO-023
SUBJECTS: list[dict] = [
    {"code": "BASIC",   "match": "物理治療基礎學", "name": "物理治療基礎學",          "sort": 1},
    {"code": "INTRO",   "match": "物理治療學概論", "name": "物理治療學概論",          "sort": 2},
    {"code": "TECH",    "match": "物理治療技術學", "name": "物理治療技術學",          "sort": 3},
    {"code": "NEURO",   "match": "神經疾病物理治療學", "name": "神經疾病物理治療學",   "sort": 4},
    {"code": "ORTHO",   "match": "骨科疾病物理治療學", "name": "骨科疾病物理治療學",   "sort": 5},
    {"code": "CARDPED", "match": "心肺疾病與小兒疾病物理治療學", "name": "心肺疾病與小兒疾病物理治療學", "sort": 6},
]
SUBJECT_BY_CODE = {s["code"]: s for s in SUBJECTS}


def subject_code_for(name: str) -> str | None:
    """把考選部的科目名稱（可能含括號補充）對到科目代碼。"""
    for s in SUBJECTS:
        if name.startswith(s["match"]):
            return s["code"]
    return None


QUESTIONS_PER_PAPER = 80
OPTION_LETTERS = ("A", "B", "C", "D")

# 標主題用的模型（可用環境變數覆蓋）
TAG_MODEL = os.environ.get("TAG_MODEL", "claude-sonnet-4-5")
TAG_BATCH_SIZE = int(os.environ.get("TAG_BATCH_SIZE", "20"))
TAG_LOW_CONFIDENCE = 0.6


def exam_dirname(roc_year: int, rnd: int) -> str:
    return f"{roc_year}-{rnd}"


def question_id(roc_year: int, rnd: int, subject_code: str, number: int) -> str:
    return f"{roc_year}-{rnd}-{subject_code}-{number:03d}"


def exam_id(roc_year: int, rnd: int, subject_code: str) -> str:
    return f"{roc_year}-{rnd}-{subject_code}"


# 行內「圖片字」：PDF 把某些字印成小圖片（多半是「𧿹」＝拇，Word 缺字）。抽字時會整個消失，
# 解析時依圖片的 MD5（PyMuPDF get_image_info(hashes=True) 的 digest）換回文字。
# 新年度出現沒見過的小圖片會發 warning，看過再加進來。2026-10 逐一目視確認。
INLINE_GLYPH_MAX = 17   # 寬、高都小於這個（pt）才當成行內字，不當附圖
GLYPH_IMAGES = {
    "08896543fb074b2aad9ef42264c52648": "拇",
    "42c924ec721cb42b0c78dfec237896b1": "拇",
    "7e3015273268de9e2290e1ffe78f121e": "拇",
    "8cf6a0c8c25cbb5b9b8ad57f4fc77401": "拇",
    "d5751a83dae8b852032804d672fb5077": "拇",
    "e68e418225d92dff4600d88fdb66302c": "拇",
    "ec6cb74004bd0363b85d1fe7fa065039": "拇",
    "fd147ab8d68c1abf65ccdade08c3a3b0": "拇",
    "98e545f2f960cb075419602f2d4cfb01": "大",      # 115-1 ORTHO 獵場看守員大拇指
    "0dc7b67256744d5bab2b7256f6b9329a": "用",      # 112-1 NEURO 運用
    "c000fada70a966dd3f1904f3b4ffe775": "V\u0307E",  # 106-1 CARDPED 分鐘換氣量 V̇E
    "88177a0f0fc29b6381656442b2652bd4": "V\u0307",   # 107-2 BASIC V̇
    "17fd3041e5ba186d5508086b57217559": "V\u0307/Q",  # 114-1 BASIC V̇/Q
}
