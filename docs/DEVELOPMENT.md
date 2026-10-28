# 開發說明

題庫怎麼從考選部的 PDF 整理成 App 用的 SQLite：指令、發布流程、目錄結構。資料格式、主題標記規則與考選部網站／PDF 的坑看 [PIPELINE.md](PIPELINE.md)。

## 環境

```
python3 -m venv .venv && source .venv/bin/activate && pip install -r requirements.txt
```

常用指令也包在 `Makefile`（`make fetch`、`make test`…）。

## 管線

```
考選部 PDF ──fetch──▶ data/raw/{年}-{次}/{科}.{Q|S|M}.pdf
            ──parse──▶ data/json/{年}-{次}/{科}.json  (+ data/figures/*.webp)
            ──validate▶ data/review/review_queue.csv   （人工看）
            ──tag────▶ 回寫 topic / secondary_topics / tags / alt_topic 到 JSON（子代理分批或 API）
            ──build──▶ dist/questions.sqlite + dist/manifest.json
```

| 指令 | 做什麼 |
|---|---|
| `python -m pipeline.fetch --years 105-115` | 抓缺的試題；**最近 4 回合的答案與更正答案一律重抓比對**，有變動寫進 `data/raw/changes.log`；想全部重抓用 `--refresh all` |
| `python -m pipeline.parse` | 解析 `needs_parse` 的科目成 JSON，附圖裁成 WebP。`--dump some.pdf` 看抽出的文字 |
| `python -m pipeline.validate` | 結構檢查，產出審核清單 |
| `python -m pipeline.tag --export-batches data/tagging/full/batches [--only 105-1/NEURO --limit 40 --force]` | 產生子代理批次檔（一份試卷一檔＋manifest.json）；子代理把結果寫到同層的 `results/` |
| `python -m pipeline.tag --import-results data/tagging/full/results --batches-dir data/tagging/full/batches` | 檢查並匯入子代理結果（`--batches-dir` 預設 `data/tagging/batches`） |
| `python -m pipeline.tag --subject NEURO --limit 40` | 用 Claude API 直接標（需 `ANTHROPIC_API_KEY`，備用） |
| `python -m pipeline.tag --apply-overrides` | 把 `data/review/topic_overrides.csv` 的人工修正寫回 |
| `python -m pipeline.taxonomy_doc` | 由 `taxonomy/taxonomy.yaml` 產生 `docs/TAXONOMY.md` |
| `python -m pipeline.build --version 2026.10.16-1` | 打包 SQLite 與 manifest |
| `python -m pytest -q` | 離線測試（用 108-1 神經科真實文字當 fixture） |

push 到任何分支，GitHub Actions 都會跑 pytest 與 `validate --strict`。

## 發布

```
git tag v2026.10.16-1 && git push --tags
```

GitHub Actions 會跑測試、`validate --strict`、build，然後把 `questions.sqlite` 與 `manifest.json` 掛到 Release；tag 以 `-test` 結尾時發成 pre-release。
Release 說明由 `python -m pipeline.release_notes` 從題庫自動產生（對外、給考生看；會和上一版 manifest 比對列出新增試卷），不用手寫。
App 固定讀 `https://github.com/JaroCho1030/ptking-questionbank/releases/latest/download/manifest.json`。

## 目錄

```
pipeline/   config / textnorm / fetch / answers / parse / validate / tag / build / release_notes / taxonomy_doc
taxonomy/   taxonomy.yaml  主題分類表（依考選部命題大綱；給人看的版本是 docs/TAXONOMY.md）
docs/       DEVELOPMENT.md（本檔）、PIPELINE.md（資料格式與規則）、TAXONOMY.md（分類表）
data/       raw（gitignore）json figures review
site/       App 的隱私政策與支援頁（GitHub Pages，見下方「網站」）
tests/      fixtures 是真實 PDF 抽出的文字
```

## 網站

`site/` 是 App 的隱私政策與支援頁，push 到 main 時由 `.github/workflows/pages.yml` 發布到 GitHub Pages：

- 隱私政策：https://jarocho1030.github.io/ptking-questionbank/privacy/
- 支援：https://jarocho1030.github.io/ptking-questionbank/support/

App Store Connect 與 App 內的連結都指向這兩個網址，路徑不要改。中英文各一段，改內容兩段都要改；隱私政策改了要更新生效日期。
