# 題庫管線說明

指令用法見 [DEVELOPMENT.md](DEVELOPMENT.md)；這份記錄**資料格式、主題標記規則**，以及考選部網站與 PDF 的各種坑。
App 端怎麼使用題庫（篩選、統計、更新）不在這裡。

## 題目 ID 與科目代碼

`{民國年}-{第幾次}-{科目代碼}-{題號三位}`，例如 `113-2-ORTHO-045`。**發布後不改**：題目內容修正（錯字、答案更正）只改欄位。

| 代碼 | 考選部科目名稱 |
|---|---|
| BASIC | 物理治療基礎學（包括解剖學、生理學、肌動學與生物力學） |
| INTRO | 物理治療學概論（包括物理治療史、物理治療倫理學與物理治療行政管理學） |
| TECH | 物理治療技術學（包括電療學、熱療學、操作治療學與輔具學） |
| NEURO | 神經疾病物理治療學 |
| ORTHO | 骨科疾病物理治療學 |
| CARDPED | 心肺疾病與小兒疾病物理治療學 |

## 題庫 JSON（中間格式，進 git）

`data/json/{年}-{次}/{科目}.json`：

```json
{
  "exam": {"id": "108-1-NEURO", "roc_year": 108, "round": 1, "subject_code": "NEURO", "exam_code": "108030",
           "class_code": "311", "paper_code": "4311", "source_q": "...", "source_s": "...", "source_m": "...",
           "answer_sha256": "...", "correction_sha256": "...", "parsed_at": "...", "warnings": []},
  "questions": [{
    "id": "108-1-NEURO-027", "number": 27, "stem": "關於小洞中風…", "options": {"A": "…", "B": "…", "C": "…", "D": "…"},
    "official_answer": "C", "accepted_answers": ["A", "C"], "is_voided": false, "correction_note": "第27題答Ａ、Ｃ給分。",
    "has_figure": false, "figure": null, "figure_kind": null, "page": 5,
    "group_id": null, "group_pos": null,
    "topic": "NEURO-STROKE", "secondary_topics": ["BASIC-ANA-NEU"], "tags": ["lacunar stroke"], "topic_confidence": 0.92, "topic_source": "llm",
    "review_flags": []
  }]
}
```

- `accepted_answers` 是最終承認的答案（可能多個）；送分題為空、`is_voided = true`。`official_answer` 是原公布答案。
- `tags` 是自由標籤（搜尋與顯示用），和主題無關、不參與統計。
- **選項是圖片的題目**（例：113-1-NEURO-031）：選項文字為空字串、附圖是 A→D 由上而下拼好的圖；App 顯示「A（見附圖）」。不手動改 JSON（`parse --all` 會覆蓋，只保留主題標記）。
- SQLite 的完整 DDL 在 `pipeline/build.py`（`schema_version` 只在表結構不相容時 +1）。

## 主題標記

### 複數主題的規則

有些題目橫跨兩個知識點（例：神經科試卷考步態週期的肌肉活動，既是步態、也是基礎學的肌動學），單一主題會讓分類搖擺。

- 每題 **1 個主要主題 + 0–2 個次要主題**。次要主題**只給真的橫跨兩個知識點**的題目；目標全題庫帶次要主題的 **< 10%**（全標結果 1.2%）。
- **低信心不加次要主題**：主要主題拿不準時只給低信心，不拿次要主題當備案（備案會讓只考 X 的題被算進 Y，污染統計）。
- **看考點、情境不算**：考量表內容或計分 → 只標評估主題；考疾病特徵或病程 → 只標疾病主題；題幹出現哪種病人只是情境，不另掛。
- **純解剖跨科**：本科試卷單純問構造或支配範圍（例：食指掌側感覺由哪條神經支配）→ 主要主題放本科（NEURO-PNI）、次要掛 `BASIC-ANA-NEU`；問損傷後的表現就只標本科。
- 主要主題**先看本科**：本科有「專門」涵蓋這個考點的主題就留本科；本科只有籠統或勉強相關的主題（概論類、「基礎與原理」、內外科概論、兒童發展這類大類），而別科有專門講這個考點的主題時，選別科。
  - 跨科例：概論試卷考良性陣發性姿勢性眩暈 → NEURO-VESTIB；心肺小兒試卷考站姿動作控制 → NEURO-BALANCE；骨科試卷考 Kaltenborn 肩關節鬆動 → TECH-JOINTMOB；骨科試卷考小孩腳趾內向 → CARDPED-PED-OTHER；心肺試卷考洗手時機 → INTRO-SAFETY；技術學試卷考 Wernicke 失語症 → BASIC-ANA-NEU。
  - 留本科例：心肺試卷考冠狀動脈分支 → CARDPED-CV-FOUND；骨科試卷考脊椎韌帶扭傷 → ORTHO-LUMBAR；神經解剖 → NEURO-FOUND；食指感覺支配 → NEURO-PNI＋次要 BASIC-ANA-NEU。
  - 跨科的題不另掛本科次要主題。次要主題不限科，但不可與主要主題相同、不可重複。`questions.topic_code` 可以是別科的 code。
  - 規則文字還沒完全抓到人工判斷（2026-10-19）：信心 0.5 且第二候選在別科的 198 題由人工逐題判（改用別科 102 題），模型照規則判同一批只有約一半一致，所以這類題不交給模型重判；信心 0.6 的同型 346 題維持模型結果。
- 資料：JSON 的 `secondary_topics`；SQLite 的 `question_topics(question_id, topic_code, rank)`，rank 0 = 主要、1–2 = 次要。
- 給人看的分類表：[TAXONOMY.md](TAXONOMY.md)（由 `taxonomy/taxonomy.yaml` 產生）。

### 標記流程

模型回傳主要主題、0–2 個次要主題（可跨科，enum 為全部 topic code）、信心分數與 `alt_topic`（第二可能的主題，只存 JSON、給人工審核二選一）。兩種跑法共用同一份提示詞與同一套結果檢查（`tag.apply_result`）：

- **子代理分批（全標用這個，不需要 API key）**：`tag --export-batches` 每份試卷產一個批次檔（提示詞＋分類表＋題目＋輸出格式）→ 一份試卷一個 Sonnet 子代理，只讀批次、寫結果 JSON，不直接改 `data/json` → `tag --import-results <results> --batches-dir <batches>` 檢查（code 必須在分類表、次要主題清理、缺題列出）後寫進 JSON。`data/tagging/` 不進 git。好處：Mac 睡著不影響、多個代理不會同時改同一個 JSON。
- **Claude API + tool use**（enum 限制 code）：備用，需要 `ANTHROPIC_API_KEY`。
- `parse` 重跑會保留 topic、tags、topic_confidence、topic_source、secondary_topics、alt_topic。
- 人工覆寫：`data/review/topic_overrides.csv`（多一欄 `secondary`，用 `|` 分隔）→ `tag --apply-overrides`，寫成 `topic_source = human`、信心 1.0；人工覆寫的題目 `--force` 不會動。
- **信心刻度**：0.95 只可能是這個／0.8 主要是這個／0.6 較核心但另一個也說得通／0.5 兩個同樣合理選較核心的／0.3 拿不準可能標錯。
  審核門檻 `TAG_LOW_CONFIDENCE = 0.45`：只審「可能標錯」的（目標全題庫 ≤ 500 題）；0.5–0.6「兩個都合理」自動接受，想抽查可依 alt_topic 篩。
- **分類表欄位**：每個主題有 `definition`（包含什麼）與選填的 `excludes`（容易混淆、要歸別處的內容，寫成「內容 → 該歸的 code」），App 顯示的名稱只留內容提示。這兩欄連同 keywords 放進標記提示詞（本科主題才放；別科只給 code 與名稱）。
- **全標結果（2026-10-19）**：10556 題子代理＋4 題人工。信心 0.95×4864、0.8×3132、0.6×1870、0.5×663、0.3×26；跨科主要主題 183 題、帶次要主題 129 題；100 個主題都有題目（最少 NEURO-INFECTION 12 題、最多 CARDPED-PED-NEURO 302 題）。

## 人工審核

- `validate` 產 `data/review/review_queue.csv`；審過的記在 `data/review/reviewed.csv`（id, reasons, reviewed_at, note），**同 id 同原因**下次不再列出，原因變了（例如重新 parse 多了新問題）會再出現。
- 主題審核不靠這個：人工改過或確認過的主題寫進 `topic_overrides.csv`，本來就不會再進清單。

## 答案更正

考選部可能在公布答案後再公告更正，甚至更正多次。`fetch` 每次執行都重新掃描最近 4 回合的結果頁、重抓 S/M 檔比對 sha256；有變動寫 `changes.log` 並標 `needs_parse`，`parse` 保留既有主題標記、只更新答案欄位。

- 「除未作答者不給分外，其餘均給分」= 四個選項都算對、未作答算錯（不是送分）；`is_voided` 只用於「一律給分」。
- 更正備註措辭不固定，`answers.py` 的 regex 涵蓋目前看過的寫法；解析不了的標 `needs_review`。

## 詳解生成

每題一則 AI 寫的三段詳解，隨題庫一起發布；App 離線顯示，並標示「AI 產生，可能有誤」。**寧缺勿濫**：審稿沒通過的題不進 SQLite，App 就沒有詳解鈕。

### 範圍與格式

- 範圍：110-1 起（`EXPLAIN_MIN_YEAR = 110`）；更早的之後再補。送分題、多正解、題組、附圖題都生成。
- 三段（JSON 欄位）：`why_correct`（為什麼是這個答案，2–3 句、≤ 120 字；多正解 ≤ 160 字）、`why_others`（其他每個選項各一句為什麼不對，`{"A": "…"}`，只含非正解選項、每項 ≤ 40 字）、`takeaway`（考點或記法一句，≤ 50 字）。
  - 字數算法（`explain.text_len`）：中文字與標點各算 1，一個英文單字或一串數字算 1，空白不算。上限常數在 `config.py`（`EXPLAIN_MAX_*`）。格式檢查容許超過上限 20%（`EXPLAIN_LENGTH_TOLERANCE`；120 → 144、160 → 192、40 → 48、50 → 60），給寫手的提示仍寫原上限。只因格式不通過的題，規則放寬後變合格的會自動重跑（沒審過，不能直接改成通過）。
- 精確細節（量表第幾項、清單項數、天數或年齡範圍）沒把握就不寫；考點不列不完整的清單（試跑時審稿者擋下的錯多半是這類）。
- 繁體中文，專有名詞第一次出現附英文；不引用書名、頁碼、指引年份（模型會編造出處）；不出現「AI」「我」「考選部指出」。
- 特殊題：送分題 `why_correct` 寫「考選部公告一律給分」＋爭議點、`why_others` 可空；多正解每個承認答案各說一遍、`why_others` 只含未承認的選項；答案被更正的以 `accepted_answers` 為準、可提一句原公布答案為何不對；題組整組題幹背景一起給、每題各一則；附圖題把圖一起給（寫手與審稿者都看）。

### 流程（`python -m pipeline.explain`，Claude API，需 `ANTHROPIC_API_KEY`）

每題三個呼叫，走 Message Batches API（半價、不佔前景）。第一批送全部寫手與盲解（互不依賴），完成後第二批送比對。
三個呼叫都用 tool use 固定輸出欄位（`strict: true`）；Opus 5.5 不接受強制 `tool_choice`，所以用 `auto`、提示詞指定工具，沒呼叫工具當該呼叫失敗。

1. **寫手**：題目（＋題組背景、附圖）、`accepted_answers`、`correction_note`、主題名稱 → 照格式輸出 JSON（tool use 限制欄位）。
2. **審稿者盲解**：只給題目（＋附圖），**不給答案與詳解** → 回 `answer`（可多個）與 `confidence`。
3. **審稿者比對**：再給 `accepted_answers`、自己的盲解結果與寫手的詳解 → `verdict`：`pass`／`reasoning_error`（推理或事實有錯）／`answer_disputed`（認為官方答案本身有問題），附一句理由。寫手輸出格式不合格（缺選項、超過字數）的題不送比對，直接不通過。

通過＝盲解答案 ⊆ `accepted_answers`（送分題不比答案）**且** `verdict = pass` **且**格式合格。其餘 `status = rejected`，原因（`review.fails`：`blind_mismatch`／`reasoning_error`／`answer_disputed`／`format`）留在 JSON 給人工看。
某個呼叫在批次裡出錯（errored、沒呼叫工具）→ 寫手有產出就存成 `pending`，下次執行自動重跑三個呼叫。

- 模型：`EXPLAIN_MODEL`（`config.py`）；寫手與審稿者同一個模型但各自獨立對話。思考深度 `EXPLAIN_EFFORT`（預設 `medium`，可用環境變數覆蓋）。
- `--dry-run` 只印會送出的題數與第一題的三個提示詞；`--limit N` 最多送 N 題；`--years 113-115` 只做這幾年。
- 續跑：批次 id 與中間結果存 `data/explanations/.batches/state.json`（不進 git）；中途斷掉再執行一次就從斷掉的批次接續（不重送），跑完改名成 `run-<時間>.json` 留底。
- 先用 `--only 114-2/NEURO` 跑一份試卷看品質與用量，再全跑。`--force` 重跑已通過的題；人工寫的（`source = human`）不會被動到。
- 重生成條件：JSON 存的 `answers_hash`（`sha256("{排序後的 accepted_answers 以逗號連接}|{is_voided 0/1}")` 前 12 碼）和 `data/json` 現況不同（答案被更正）→ 下次執行自動重寫、重審。`rejected` 且 hash 一致的不會自動重跑（改了提示詞要重跑用 `--force`）。

### 子代理分批（不需要 API key；`python -m pipeline.explain_batches`）

規則、提示詞、通過條件都和 API 版相同，只是三個角色各自是一個子代理批次，結果寫成 JSON 檔再匯入：

1. `--export DIR --years 110-112 [--only 110-1/NEURO] [--limit N] [--force] [--batch-size 40]`：每份試卷切成 40 題一批（`<年-次>_<科目>_<序號>`），產生 `DIR/writer/*.md`（題目＋答案＋主題）與 `DIR/blind/*.md`（**只有題目**），附圖轉成 `DIR/figures/<id>.png` 讓子代理用 Read 看；`DIR/manifest.json` 記每批的題目 id 與 `answers_hash`。
2. 一批一個子代理（寫手與盲解互不依賴，可同時開）：只讀那份批次檔與附圖，結果寫到 `DIR/results/<角色>/<批次>.json`（`{"batch": …, "results": [{id, …}]}`；寫手的 `why_others` 是 `{"A": "…"}` 物件）。盲解的批次檔沒有答案，子代理也被要求不讀題庫與其他檔案。
3. `--export-check DIR`：寫手格式合格、盲解有結果的題才產生 `DIR/check/*.md`（含承認答案、盲解結果與待審詳解）；格式不合格的直接不通過。再開一批子代理審。
4. `--import DIR [--model 標示]`：三種結果合併、照同一套 `judge` 判定後寫進 `data/explanations`；缺題、多題、格式不對只列警告、那題不寫（重跑那份批次再匯入即可）。寫手有、審稿還沒有的題先存成 `pending`。
   `model` 欄位預設 `subagent writer=sonnet review=opus`（2026-11 跑 110–112 用的組合：寫手 Sonnet、盲解與比對 Opus）。

110–112 年（2,880 題）用這個方式跑的結果：通過 2,581（90%）、不通過 299（推理有錯 167、盲解答錯 153、官方答案有疑 32，一題可能多個原因）；113–115 年用 API 版（Opus 5.5）通過率約 91%。

### 資料

- `data/explanations/{年}-{次}/{科目}.json`，進 git（build 要能重現）；`parse` 不碰這個目錄。檔案是 `{"exam_id": "114-2-NEURO", "explanations": [...]}`，依 id 排序、一題一筆：

```json
{"id": "114-2-NEURO-027", "status": "pass", "source": "llm", "model": "…", "generated_at": "…",
 "answers_hash": "…",
 "why_correct": "…", "why_others": {"A": "…", "B": "…", "D": "…"}, "takeaway": "…",
 "review": {"blind_answer": ["C"], "blind_confidence": 0.9, "verdict": "pass", "reason": "…"}}
```

  不通過的 `review` 另有 `fails`（原因代碼）與 `problems`（格式問題、批次錯誤）。

- `status`：`pass`／`rejected`／`pending`（寫了還沒審完）。人工改寫：直接改三段、`source` 設 `human`、`status` 設 `pass`（答案被更正後要人工改內容並更新 `answers_hash`）。
- `build` 只把 `status = pass` 且 `answers_hash` 與現況一致（答案沒被更正過）的題寫進 SQLite `explanations(question_id, why_correct, why_others, takeaway, model, generated_at)`；`why_others` 存 JSON 字串。`schema_version` 維持 1（舊 App 不讀這張表照常可用）。manifest 加 `explanation_count`。
- `validate` 檢查：status 合法、題目存在；`status = pass` 的欄位齊、字數上限、`why_others` 的 key 都是該題存在且非正解的選項、非送分題每個非正解選項都有（以上錯了算結構錯誤，`--strict` 會失敗）；`pass` 的 `answers_hash` 與現況不一致 → 審核清單（原因 `explanation_stale`）。

## 考選部網站與 PDF（2026-10 觀察）

- 結果頁 `wFrmExamQandASearch.aspx?y=<西元年>&e=<考試代碼>`；考試代碼每年不同（108030、113020、115090…），含「物理治療師」的考試每年兩個（第一次約 1–2 月、第二次約 7 月），從下拉選單解析。105 年考試名稱是「物理職能呼吸治療師」（`_PT_EXAM_RE`）。
- 檔案 `wHandExamQandA_File.ashx?t=<Q|S|M>&code=&c=&s=&q=1`，`t=M` 是更正答案，只在有更正時出現；科目參數 `s` 不固定（11/22/…或 0701/0702/…），依科目名稱對應。
- 試題 PDF 文字層完整；部分年份字型把常用字映射到康熙部首區段（⽤ → 用），`textnorm.fix_radicals` 處理。
- 答案 PDF 是 20 題一列的全形字母；更正檔用 ＃ 標記並在備註說明。
- 圈號字型壞掉（①–⑥ 被對到西里爾字母 U+045B–，pdfplumber 直接丟掉）→ 由 PyMuPDF 取回位置補回；疊印粗體 → 抽字前 `dedupe_chars()`。每頁比對兩個抽取器的圈號數，不一致就發 warning。
- **附圖**：以「題目範圍內有圖形/圖片物件」判定（不靠「如下圖」提示字），範圍從本題題號到下一題題號、可跨頁，多頁時上下拼成一張（`figure_kind = graphic_multipage`）。存 WebP 品質 85、2 倍解析度（全寬約 1100px），十年約 7 MB，進 SQLite `figures` 表。有提示字但找不到圖形 → 不裁圖、進審核清單。
  - 裁切框：圖形範圍外擴 8pt 留白，但碰到圖外的文字行（圖下方的選項 A、圖上方的題幹最後一行）就收回到那行之外（`parse.trim_clip`）；圖片框壓進文字行 ≤ 3pt 也算圖外。跟圖垂直重疊的字（圖旁標籤、選項照片的 A/B 代號）不動。
  - 選項代號（「A.」）框進附圖（`parse.include_option_labels`）時，上下放寬不越過本題範圍（本題題號到下一題題號）與圖上下別題的題號行（107-2-TECH-076 曾把第 77、78 題框進來）。
  - 附圖被切成上百條 1pt 圖片 → 先合併（`merge_strips`）。
- **抽字的其他坑**（對照表都在 `config.py`，遇到沒見過的會發 warning）：
  - 行內圖片字：字被印成小圖片（多半是「𧿹」＝拇），依圖片 MD5 換回文字（`GLYPH_IMAGES`）。行內公式也可能是圖片（V̇/Q̇、PaCO₂…，寬 20–75pt、高 16–18pt）：已知 digest 不論大小都當字；一行字高度（< 19pt、寬 < 120pt）但沒見過的發 warning。
  - CJK 部首補充區（⻑ ⺠ ⻄）NFKC 不處理，`textnorm._RADICAL_SUPPLEMENT` 手動對照；validate 會把殘留的部首字列進審核清單。
  - 壞字形：105–107 年少數字用 Gulim 排、cmap 壞掉只剩 U+FFFD（例：105-1 INTRO 第 60 題「不是」的「不」消失、題意相反），依「字型:gid」換回（`BROKEN_GLYPHS`）。私用區 U+EEE7 → 拇；C1 控制碼刪除。
  - 上下標：字級較小且偏移的英數字換成 Unicode 上下標並拉回同一行（`normalize_scripts`，H₂O、FEV₁、Ca²⁺）；沒有對應 Unicode 的整段保持原字。
  - 選項代號與文字被拆成兩行 → 該頁放寬 y_tolerance 重抽；疊印字字級有浮點誤差 → dedupe 只比字型。
  - 英文旁的彎撇號一律換直撇號（中文字型會顯示成全形）。
- **題組**：`parse.assign_groups()`——題幹以「承上」開頭 → 與上一題同組（可連續）；題幹含「回答以下／下列 N 題」→ 本題起連續 N 題同組。`group_id` 是組內第一題的 id，`group_pos` 從 1 起算；validate 檢查題組連續、編號正確。「承上題，此圖…」這種題幹直接提到圖的，共用前題的圖（`figure_kind = inherited`）。105–115 年共 67 組 139 題。
