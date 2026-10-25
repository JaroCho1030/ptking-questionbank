# ptking-questionbank — 給 Claude Code 的說明

- 語言：溝通用繁體中文；程式註解繁體中文、識別字英文。
- **這是公開 repo**：程式、commit 訊息、Release 說明都看得到。不要寫任何會透露作者身份的內容（真名、學校、email、暱稱、本機路徑）；
  commit 不加 Claude-Session 連結；提到人一律用「人工」「作者」。
- 說明文件：指令看 `README.md`；資料格式、主題標記規則、考選部網站與 PDF 的坑看 `docs/PIPELINE.md`。規則改了要同步改這份。
- 進入點都是 `python -m pipeline.<step>`。改程式一定要跑 `python -m pytest -q`（fixture 是真實 PDF 抽出的文字，不需要網路）；
  push 到任何分支 GitHub Actions 也會跑 pytest 與 `validate --strict`。
- 考選部網站只能從作者的電腦存取，不要在沙盒裡試抓（`pipeline.fetch`）。
- 題目 ID `{民國年}-{次}-{科目代碼}-{題號三位}`，發布後不可變。答案可能被更正：`accepted_answers` 可多個、`is_voided` 送分題。
- 分類一律改 `taxonomy/taxonomy.yaml`，再跑 `python -m pipeline.taxonomy_doc` 產生 `docs/TAXONOMY.md`（測試會檢查兩者同步）。
- 人工改過的主題寫 `data/review/topic_overrides.csv` → `tag --apply-overrides`；不要直接改 JSON 的主題（`parse --all` 只保留主題標記，其他欄位會被覆蓋）。
- 發布：在 main 打 tag `vYYYY.MM.DD-N` 並 push，Actions 會 build 並發 Release；App 讀 latest Release 的 `manifest.json`。
- commit 訊息：繁體中文，前綴 `feat:`／`fix:`／`data:`／`test:`／`docs:`／`chore:`。
- `site/`：App 的隱私政策與支援頁（GitHub Pages，`pages.yml` 發布）。網址路徑不可改（App 與 App Store Connect 都連過來）；中英文兩段同步改；隱私政策改內容要改生效日期。
