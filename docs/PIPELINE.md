# 題庫管線說明

指令用法見 [README](../README.md)；這份記錄**資料格式、主題標記規則**，以及考選部網站與 PDF 的各種坑。
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

## 考選部網站與 PDF（2026-10 觀察）

- 結果頁 `wFrmExamQandASearch.aspx?y=<西元年>&e=<考試代碼>`；考試代碼每年不同（108030、113020、115090…），含「物理治療師」的考試每年兩個（第一次約 1–2 月、第二次約 7 月），從下拉選單解析。105 年考試名稱是「物理職能呼吸治療師」（`_PT_EXAM_RE`）。
- 檔案 `wHandExamQandA_File.ashx?t=<Q|S|M>&code=&c=&s=&q=1`，`t=M` 是更正答案，只在有更正時出現；科目參數 `s` 不固定（11/22/…或 0701/0702/…），依科目名稱對應。
- 試題 PDF 文字層完整；部分年份字型把常用字映射到康熙部首區段（⽤ → 用），`textnorm.fix_radicals` 處理。
- 答案 PDF 是 20 題一列的全形字母；更正檔用 ＃ 標記並在備註說明。
- 圈號字型壞掉（①–⑥ 被對到西里爾字母 U+045B–，pdfplumber 直接丟掉）→ 由 PyMuPDF 取回位置補回；疊印粗體 → 抽字前 `dedupe_chars()`。每頁比對兩個抽取器的圈號數，不一致就發 warning。
- **附圖**：以「題目範圍內有圖形/圖片物件」判定（不靠「如下圖」提示字），範圍從本題題號到下一題題號、可跨頁，多頁時上下拼成一張（`figure_kind = graphic_multipage`）。存 WebP 品質 85、2 倍解析度（全寬約 1100px），十年約 7 MB，進 SQLite `figures` 表。有提示字但找不到圖形 → 不裁圖、進審核清單。
  - 裁切框：圖形範圍外擴 8pt 留白，但碰到圖外的文字行（圖下方的選項 A、圖上方的題幹最後一行）就收回到那行之外（`parse.trim_clip`）；圖片框壓進文字行 ≤ 3pt 也算圖外。跟圖垂直重疊的字（圖旁標籤、選項照片的 A/B 代號）不動。
  - 附圖被切成上百條 1pt 圖片 → 先合併（`merge_strips`）。
- **抽字的其他坑**（對照表都在 `config.py`，遇到沒見過的會發 warning）：
  - 行內圖片字：字被印成小圖片（多半是「𧿹」＝拇），依圖片 MD5 換回文字（`GLYPH_IMAGES`）。行內公式也可能是圖片（V̇/Q̇、PaCO₂…，寬 20–75pt、高 16–18pt）：已知 digest 不論大小都當字；一行字高度（< 19pt、寬 < 120pt）但沒見過的發 warning。
  - CJK 部首補充區（⻑ ⺠ ⻄）NFKC 不處理，`textnorm._RADICAL_SUPPLEMENT` 手動對照；validate 會把殘留的部首字列進審核清單。
  - 壞字形：105–107 年少數字用 Gulim 排、cmap 壞掉只剩 U+FFFD（例：105-1 INTRO 第 60 題「不是」的「不」消失、題意相反），依「字型:gid」換回（`BROKEN_GLYPHS`）。私用區 U+EEE7 → 拇；C1 控制碼刪除。
  - 上下標：字級較小且偏移的英數字換成 Unicode 上下標並拉回同一行（`normalize_scripts`，H₂O、FEV₁、Ca²⁺）；沒有對應 Unicode 的整段保持原字。
  - 選項代號與文字被拆成兩行 → 該頁放寬 y_tolerance 重抽；疊印字字級有浮點誤差 → dedupe 只比字型。
  - 英文旁的彎撇號一律換直撇號（中文字型會顯示成全形）。
- **題組**：`parse.assign_groups()`——題幹以「承上」開頭 → 與上一題同組（可連續）；題幹含「回答以下／下列 N 題」→ 本題起連續 N 題同組。`group_id` 是組內第一題的 id，`group_pos` 從 1 起算；validate 檢查題組連續、編號正確。「承上題，此圖…」這種題幹直接提到圖的，共用前題的圖（`figure_kind = inherited`）。105–115 年共 67 組 139 題。
