# tw-cb-data — 台股可轉換公司債歷史資料庫

自建的台股 CB 歷史資料庫。櫃買中心的條款頁與下櫃頁都是**滾動視窗快照**，個券到期或被提前贖回後就查不到，因此必須自己留存。

---

## 驗證狀態

本文件所有櫃買端點皆於 **2026-08-26** 從台灣本機 IP 以瀏覽器實地驗證，非推測。
MOPS 部分尚未驗證，標示為「待驗證」，請先跑 `scripts/probe.py`。

---

## 1. 櫃買 TPEx — 已驗證

### 1.1 索引 API（取得某月的檔案清單）

```
POST https://www.tpex.org.tw/www/zh-tw/bond/cbDaily
Content-Type: application/x-www-form-urlencoded

date=2026/08/25     ← 西元 YYYY/MM/DD，用民國會出錯
fileCode=rsta0113   ← 全小寫
id=                 ← 空字串，但必須送
response=json
```

**是 POST，不是 GET。** 用 GET 帶 query string 會回 `{"stat":"參數輸入錯誤"}`。

`date` 決定的是**月份**，不是單日 —— 送任一天會回傳該月全部交易日的檔案清單。所以要列舉全部歷史檔案，只需 236 次請求（2007-01 至今每月一次），不是每天一次。

回應：

```json
{
  "stat": "ok",
  "date": "20260831",
  "tables": [{
    "title": "轉(交)換債日統計報表",
    "fields": ["資料日期", "檔案下載"],
    "data": [
      ["115/08/25", "/storage/bond_zone/tradeinfo/cb/2026/202608/RSta0113.20260825-C.csv"],
      ["115/08/24", "/storage/bond_zone/tradeinfo/cb/2026/202608/RSta0113.20260824-C.csv"]
    ]
  }]
}
```

清單內的日期是**民國**，檔名內的日期是**西元**。

### 1.2 fileCode 目錄

| fileCode | 報表 | 用途 |
|---|---|---|
| `rsta0113` | 每日轉(交)換公司債買賣斷交易行情表 | **主行情，優先** |
| `rsta0211` | 每日轉(交)換公司債買賣斷券商買賣日報表 | 券商別 |
| `rsrdp010` | 每日轉(交)換公司債附條件交易行情表 | 附條件 |
| `rsta0215` | 每日交易概況 | 市場總量 |
| `rsta0163` | 每日附認股權公司債行情表 | 附認股權（非 CB） |
| `cbdrs001` | 轉換公司債資訊看板 | 條款類 |

### 1.3 靜態檔路徑（可繞過 API）

```
https://www.tpex.org.tw/storage/bond_zone/tradeinfo/cb/{YYYY}/{YYYYMM}/RSta0113.{YYYYMMDD}-C.csv
```

路徑完全由日期推導，**不需要先打 API**。索引 API 的價值只在於它能告訴你哪幾天有檔（免去自行判斷交易日與休市日）。

注意大小寫不一致：檔名是 `RSta0113`（R、S 大寫），參數是 `rsta0113`（全小寫）。

**歷史起點 2007-01-02，已驗證。** 2007-01 的索引回 22 個檔，最舊為 `RSta0113.20070102-C.csv`。

### 1.4 檔案格式 —— 直接 `read_csv` 會壞

編碼 **Big5**（用 UTF-8 解會整片亂碼）。這不是普通 CSV，**每行第一欄是列型標籤**：

```
TITLE,櫃檯買賣轉(交)換公司債買賣斷交易行情表-含議價及鉅額交易
DATADATE,日期:115年08月25日
ALIGN,C,L,C,R,R,R,R,R,R,R,R,R,R,R,R
HEADER,代號,名稱,交易,收市,漲跌,開市,最高,最低,筆數,單位,金額,均價,明日參價,明日漲停,明日跌停
BODY,"11011","台泥一永  ","等價","","","","","","","","","101.12 ","101.40 ","111.50 ","91.30  "
BODY,"","","議價","","","","","","","","","","","",""
```

處理要點：

- 只取 `BODY` 列，欄名取自 `HEADER` 列（都要去掉開頭的標籤欄）
- **一檔 CB 佔兩列**：`等價` 與 `議價`。**代號與名稱只出現在第一列**，第二列是空字串，必須前向填補
- 數值欄位帶尾隨空白，要 `strip()`
- 名稱欄以全形空白補齊，也要 `strip()`
- 單日約 776 行、約 385 檔

### 1.5 其他 CB 頁面（端點已定位，參數待抓）

| 頁面 | 路徑 |
|---|---|
| 最近上櫃 CB | `/zh-tw/bond/issue/cbond/listed.html` |
| **最近下櫃 CB** | `/zh-tw/bond/issue/cbond/delisted.html` |
| 債息資料 | `/zh-tw/bond/issue/cbond/rate.html` |
| 賣回權資料 | `/zh-tw/bond/issue/cbond/option.html` |
| 三大法人買賣 CB 明細 | `/zh-tw/bond/info/statistics-cb/inst-trading/day.html` |
| 三大法人買賣 CB 彙總 | `/zh-tw/bond/info/statistics-cb/inst-summary/day.html` |
| 轉換暨累計彙總表（月） | `/zh-tw/bond/info/statistics-cb/month/overview.html` |
| 月交易彙總表 | `/zh-tw/bond/info/statistics-cb/month/statistics.html` |
| 個別債券日資訊查詢 | `/zh-tw/bond/info/statistics-cb/day-quotes.html` |
| 變更/停止交易 | `/zh-tw/bond/announce/suspend.html` |
| 停止轉換 | `/zh-tw/bond/announce/close.html` |
| 股東會停止過戶 | `/zh-tw/bond/announce/shareholder.html` |

### 1.6 下櫃頁的視窗極窄 —— 這是最急的一件事

實測 `delisted.html` 只保留 **10 筆**，涵蓋 115/08/10 至 115/08/26，約 **16 天**。

也就是說這頁根本留不住歷史，過去十九年的下櫃事件早已從上面掉光。

- **往前**：只能從日行情檔反推 —— 某代號連續數日從日檔消失即視為已下櫃，再與最後交易日對齊。
- **往後**：必須至少每週快照一次，最好每日。漏一個月就永久少一批下櫃事件。

`listed.html`（上櫃）推測同樣是窄視窗，尚未實測筆數。

---

## 2. MOPS — 已部分驗證（2026-08-26）

主機用 **`mopsov.twse.com.tw`**。`mops.twse.com.tw` 會導到 error 頁。

`mopsov` 的 robots.txt disallow `/mops/`，所以 MOPS 一律在本機跑，不放 GitHub Actions。

### 2.1 編碼是 UTF-8，不是 Big5

前一份交接文件寫 Big5，**實測是 UTF-8**。用 Big5 解會整片亂碼。

### 2.2 包裹下載（已驗證可用）

```
POST https://mopsov.twse.com.tw/mops/web/ajax_t120sb02

encodeURIComponent=1
firstin=true
step=12            ← 包裹下載；查詢是 11
TYPEK=all
bond_kind=5,7      ← 轉(交)換公司債。不給這個會混進普通公司債與金融債券
nh=n               ← 最近三個月（對應頁面 t120sb02_q10）
issuer_stock_code_1=1000
issuer_stock_code_2=9999
```

- **至少要給一項查詢條件**，否則回「除預設項目外，請至少輸入一項查詢項目」（已重現）
- 回傳 **113 欄**的 HTML 表格（交接文件寫 132 欄，不符）
- 日期為**西元**格式

**限制：只回當月申報資料。** 實測給 `issue_date` 民國 90/1/1～115/12/31、或 `mature_date` 115～130，回傳都是同樣 3 筆、申報年月一律 202607。**日期條件不會擴大歷史範圍**，這個端點只能做每月增量，無法回補歷史。

### 2.3 113 欄中最關鍵的幾欄

| # | 欄位 | 用途 |
|---|---|---|
| 42 | 發行時認股價格 | 原始轉換價 |
| 43 | 最新認股價格 | 現行轉換價 |
| 44 | **最新認股價格生效日期** | **事件時點，回測用這個，不要用申報年月** |
| 63 | 轉換溢價率 | |
| 64 / 65 | 轉換期間起 / 迄 | |
| 30–32 | 賣回權條款、下一次賣回權日期、價格 | |
| 85 | **下櫃日期** | 補櫃買下櫃頁視窗太窄的缺口 |
| 89 / 90 | **本次重設日期 / 下次重設日期** | **向下重設（reset）直接有欄位** |
| 78–82 | 本月買回／賣回／轉換張數與股數 | |

第 89、90 欄很重要：MOPS 直接標了重設日期，代表「反稀釋 vs 向下重設」的分類**可能不必**靠標的股除權息日反推（見第 4 節第 4 點）。仍建議用除權息日交叉驗證，但主判準可以直接用這兩欄。

### 2.4 t120sg01 —— 尚未打通

實測回 **「年月參數錯誤」**，不是交接文件說的「債券期參數錯誤」。

而且送 `bond_yrn=1&bond_subn=$M00000001` 與完全不送，**兩次回應 byte 完全相同**，代表那組參數根本沒進到判斷式就先被年月檢查擋掉了。所以交接文件關於 `bond_yrn` / `bond_subn` 的推導規則**目前無法確認**。

下一步是找出年月參數的正確名稱與格式。頁面 `t120sg01` 上只有全站搜尋表單，真正的查詢表單是 JS 載入的，要再挖一次。

### 2.5 其他

- MOPS 附件 PDF 會 404，發行辦法要用就當下鏡像存檔，不能只存連結（沿用前份文件，未複驗）

---

## 3. 其他來源

- 集保 `opendata.tdcc.com.tw/getOD.ashx?id=2-8` 只回當月，不存就沒了
- cb168 `https://cb168.netlify.app/output.xlsx`（約 74KB，2,322 檔含已下市），欄位僅代號／名稱／最高／最低／振幅。可與官方母體 2,412 檔互相校驗

---

## 4. 尚未解決

1. **靜態檔能否用純 Python 抓** —— 瀏覽器內可以，但不確定是否依賴 session cookie 或 referer 檢查。`probe.py` 會測
2. **GitHub Actions 的 IP 會不會被擋** —— 已知 Anthropic 雲端 proxy 打櫃買連 robots.txt 都回 403，但無法推論 Actions 是否同一批
3. **條款歷史覆蓋率不是 100%** —— 12211 久津一、15291 樂士一、62441 茂迪一 皆回「查無債券基本資料」。這批正是最極端的樣本，缺了會系統性高估報酬，回補後務必算覆蓋率並寫進方法論
4. **轉換價調整未分類** —— 反稀釋（除權息、現增，機械性）vs 向下重設（reset，帶資訊）。用標的股除權息日對一次即可分離，資料可接 `daily-postmarket`。不分開會讓訊號被大量無意義的機械調整稀釋

---

## 5. 目錄

```
scripts/tpex_cb.py    櫃買索引、下載、解析
scripts/probe.py      驗證剩餘未知
data/quotes/          日行情（YYYY/RSta0113.YYYYMMDD.csv，UTF-8 整理後）
data/snapshots/       下櫃／上櫃／債息／賣回權 每日快照
```
