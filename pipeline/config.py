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
FIGURE_DIR = DATA_DIR / "figures"   # 附圖 PNG（進 git）
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
