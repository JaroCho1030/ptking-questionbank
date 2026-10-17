# ptking-questionbank — 物理治療師國考題庫

「我要成為物治王」iOS App 的題庫資料與整理管線。資料來源是考選部
[考畢試題查詢平臺](https://wwwq.moex.gov.tw/exam/wFrmExamQandASearch.aspx)公開的試題與標準答案。

## 管線

```
考選部 PDF ──fetch──▶ data/raw/{年}-{次}/{科}.{Q|S|M}.pdf
            ──parse──▶ data/json/{年}-{次}/{科}.json  (+ data/figures/*.png)
            ──validate▶ data/review/review_queue.csv   （人工看）
            ──tag────▶ 回寫 topic / tags 到 JSON
            ──build──▶ dist/questions.sqlite + dist/manifest.json
```

| 指令 | 做什麼 |
|---|---|
| `python -m pipeline.fetch --years 105-115` | 抓缺的試題；**最近 4 回合的答案與更正答案一律重抓比對**，有變動寫進 `data/raw/changes.log` |
| `python -m pipeline.parse` | 解析 `needs_parse` 的科目成 JSON，附圖裁成 PNG。`--dump some.pdf` 看抽出的文字 |
| `python -m pipeline.validate` | 結構檢查，產出審核清單 |
| `python -m pipeline.tag --subject NEURO --limit 40` | 先小量試標；滿意再 `python -m pipeline.tag` 全跑（需 `ANTHROPIC_API_KEY`） |
| `python -m pipeline.tag --apply-overrides` | 把 `data/review/topic_overrides.csv` 的人工修正寫回 |
| `python -m pipeline.build --version 2026.10.16-1` | 打包 SQLite 與 manifest |
| `python -m pytest -q` | 離線測試（用 108-1 神經科真實文字當 fixture） |

## 發布

```
git tag v2026.10.16-1 && git push --tags
```
GitHub Actions 會跑測試、validate --strict、build，然後把 `questions.sqlite` 與 `manifest.json` 掛到 Release。
App 固定讀 `https://github.com/<owner>/ptking-questionbank/releases/latest/download/manifest.json`。

## 答案更正

考選部公布答案後常再公告更正（PDF 的 `t=M`），有時更正不只一次。處理方式：

- `fetch` 預設重抓最近 4 回合的 S/M 檔並比對 sha256；想全部重抓用 `--refresh all`。
- 更正備註的措辭不固定（「答Ａ、Ｃ給分」「一律給分」「答案更正為Ｂ」…），`answers.py` 的 regex 涵蓋目前看過的寫法；
  解析不了的會標 `needs_review`，進審核清單。
- 題目 JSON 同時保留 `official_answer`（原公布）與 `accepted_answers`（最終承認的答案，可能多個）；`is_voided` 代表送分題。

## 目錄

```
pipeline/   config / textnorm / fetch / answers / parse / validate / tag / build
taxonomy/   taxonomy.yaml  主題分類表（依考選部命題大綱）
data/       raw（gitignore）json figures review
tests/      fixtures 是真實 PDF 抽出的文字
```

## 授權

考選部試題為政府公開資訊；本 repo 的整理程式以 MIT 授權。題目若有錯誤歡迎開 issue（附題目 ID，例如 `108-1-NEURO-027`）。
