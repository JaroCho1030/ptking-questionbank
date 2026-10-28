# 物理治療師國考題庫

這是 iOS App「**我要成為物治王**」使用的物理治療師國考考古題題庫。題目與標準答案整理自考選部公開的歷屆試題，App 會自動下載最新版，離線也能刷題、模擬考、看統計。

> App 即將在 App Store 上架。上架前這裡的題庫已經可以瀏覽與下載。

## 收錄內容

- **範圍**：民國 105 年起的物理治療師專技高考，每年兩次（第一次約 1–2 月、第二次約 7 月），六科都收。
- **答案**：每題附考選部公布的標準答案；考選部事後更正的答案已改成更正後的版本，「一律給分」的送分題會標出來、不計分。
- **附圖**：題目有附圖的，從原試卷裁出圖片一起收錄。
- **主題分類**：每題依考科內容歸到一個主題（少數橫跨兩個知識點的題目另有次要主題），可以按主題練習、看自己哪裡弱。分類表見 [docs/TAXONOMY.md](docs/TAXONOMY.md)。分類由 AI 輔助、再經人工抽查修正，僅供參考。

確切的題數、各科分布與每一版的更新內容，請看 [最新 Release](https://github.com/JaroCho1030/ptking-questionbank/releases/latest)。

| 科目 | 代碼 |
|---|---|
| 物理治療基礎學（解剖學、生理學、肌動學與生物力學） | `BASIC` |
| 物理治療學概論（物理治療史、倫理學與行政管理學） | `INTRO` |
| 物理治療技術學（電療學、熱療學、操作治療學與輔具學） | `TECH` |
| 神經疾病物理治療學 | `NEURO` |
| 骨科疾病物理治療學 | `ORTHO` |
| 心肺疾病與小兒疾病物理治療學 | `CARDPED` |

## 題目編號

每題都有固定編號，格式是 `年-次-科目-題號`。例如 `108-1-NEURO-027` 代表民國 108 年第一次考試、神經疾病物理治療學第 27 題。編號一旦發布就不會改，回報錯誤時附上它最快。

## 多久更新一次

- 每次國考公布試題後，加入新的一次考試。
- 考選部公告更正答案時（有時同一次考試會更正不只一次），跟著更新。

App 開啟時會自動檢查並下載新版，不需要手動處理；已經作答過的題目，App 會依更正後的答案重新判定。

## 發現錯誤？

題目文字、答案、附圖或主題分類有誤，歡迎：

- [開 issue](https://github.com/JaroCho1030/ptking-questionbank/issues)，附上題目編號與哪裡不對；或
- 在 App 裡從「我的 › 回報問題」寄信，會自動附上 App 與題庫版本。

## App 相關頁面

- [支援與常見問題](https://jarocho1030.github.io/ptking-questionbank/support/)
- [隱私政策](https://jarocho1030.github.io/ptking-questionbank/privacy/)

## 想自己使用這份資料

每個 Release 附兩個檔案：

- `questions.sqlite`：完整題庫（題目、選項、答案、附圖、主題分類）。
- `manifest.json`：版本號、題數與檔案 sha256。

最新版的固定網址是 `https://github.com/JaroCho1030/ptking-questionbank/releases/latest/download/manifest.json`。各試卷的整理結果也以 JSON 放在 [`data/json/`](data/json/)，格式說明在 [docs/PIPELINE.md](docs/PIPELINE.md)。

想了解題庫怎麼從考選部的 PDF 整理出來、或想參與改進，請看 [docs/DEVELOPMENT.md](docs/DEVELOPMENT.md)。

## 資料來源與聲明

試題與答案取自考選部[考畢試題查詢平臺](https://wwwq.moex.gov.tw/exam/wFrmExamQandASearch.aspx)公開資料，屬政府公開資訊。本專案為個人製作，與考選部或任何政府機關無關；內容如與考選部公告不一致，以考選部公告為準。

整理程式以 MIT 授權。
