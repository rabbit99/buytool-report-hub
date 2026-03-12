# 天堂經典版 LINE 群組交易分析工具

自動解析 LINE 群組聊天記錄匯出的 `.txt` 檔案，擷取買賣訊息、物品價格，並匯出格式化的 Excel 報表。

---

## 專案結構

```
BuyTool/
├── txt/                 ← 把 LINE 匯出的 .txt 放這裡
├── out/                 ← Excel 報表輸出位置（自動建立）
├── analyze.py           ← 主程式
├── requirements.txt     ← Python 套件依賴
└── README.md            ← 本文件
```

---

## 環境準備（只需做一次）

1. 確認已安裝 **Python 3.10+**  
2. 安裝依賴套件：
   ```powershell
   cd d:\SideProject\BuyTool
   py -m pip install -r requirements.txt
   ```

---

## 如何更新聊天資料

1. 在 LINE 群組中，點選 **≡ → 設定 → 傳送聊天紀錄**，匯出 `.txt` 檔案
2. 將匯出的 `.txt` 檔案放入 `txt/` 資料夾
3. 可以放多個 `.txt`，程式會自動讀取 `txt/` 資料夾內所有 `.txt` 檔案
4. 每次有新的聊天匯出，**直接覆蓋舊檔案**或**新增檔案**到 `txt/` 即可

> **提示**：建議每次直接覆蓋同一個檔案名稱（例如固定叫 `chat.txt`），保持資料夾乾淨。

---

## 使用方式

在 VS Code 終端機（或 PowerShell）中執行：

### 基本用法（最常用）

```powershell
# 預設：讀取 txt/ 全部檔案，篩選最近 30 天
py analyze.py
```

### 指定天數

```powershell
py analyze.py --days 60      # 最近 60 天
py analyze.py --days 7       # 最近 7 天
py analyze.py --days 90      # 最近 90 天
```

### 篩選特定物品

```powershell
py analyze.py --items 大馬 鋼手 鋼靴
py analyze.py --days 14 --items 艾盔 十字 保斗
```

> 可用的物品名稱可以用 `py analyze.py --list-items` 查看，**支援別名**（如 `大馬` = `大馬士革`）

### 匯率換算

```powershell
py analyze.py --rate 170     # 1T = 170 萬天幣，自動新增換算欄
```

### 其他選項

```powershell
py analyze.py --list-items            # 列出所有可搜尋物品名稱
py analyze.py --all                   # 包含無法辨識名稱的「其他」類別
py analyze.py --keep 3                # 只保留最近 3 份存檔（預設 5）
py analyze.py --output 自訂名稱.xlsx  # 指定輸出檔名
py analyze.py --input txt/某檔案.txt  # 指定單一輸入檔
```

---

## 所有參數一覽

| 參數 | 預設值 | 說明 |
|------|--------|------|
| `--days N` | `30` | 篩選最近 N 天的資料 |
| `--input 路徑` | `txt/` 全部 | 指定單一 `.txt` 輸入檔 |
| `--items 名稱...` | 全部物品 | 只分析指定物品（支援別名） |
| `--rate N` | 無 | 天幣匯率，如 `170` = 1T=170萬天幣 |
| `--output 檔名` | `天堂交易分析_最新.xlsx` | 指定輸出檔名 |
| `--keep N` | `5` | 保留最近 N 份時間戳記存檔 |
| `--all` | 關閉 | 包含「其他」類別（預設排除） |
| `--list-items` | - | 列出所有可搜尋物品名稱後退出 |

---

## 輸出 Excel 說明

輸出到 `out/` 資料夾，包含兩個檔案：

| 檔案 | 說明 |
|------|------|
| `天堂交易分析_最新.xlsx` | 每次執行覆蓋，永遠是最新結果 |
| `天堂交易分析_YYYYMMDD_HHMMSS.xlsx` | 帶時間戳記的存檔副本 |

### Excel 分頁

| 分頁名稱 | 內容 |
|----------|------|
| **行情總覽** | 各物品統計：收購/出售筆數、去重數、價格範圍(T/萬)、日期範圍 |
| **交易明細** | 每筆交易記錄：日期、時間、物品、動作、價格、完整訊息 |
| **天幣匯率** | 天幣匯率相關訊息，追蹤匯率趨勢 |

### 顏色標記

- 🟢 綠色底色 = 收購
- 🔴 粉紅底色 = 出售
- 🟠 橙色底色 = 買賣皆有

---

## 舊檔案處理

- `天堂交易分析_最新.xlsx` 每次執行都會覆蓋
- 時間戳記存檔最多保留 5 份（可用 `--keep` 調整），超過自動刪除最舊的
- 不需要手動清理 `out/` 資料夾

---

## 快速操作流程

```
1. LINE 匯出聊天記錄 → 拿到 .txt
2. 把 .txt 丟進 txt/ 資料夾（覆蓋舊的或新增）
3. 開 VS Code 終端機，執行：
   py analyze.py --days 30
4. 到 out/ 資料夾開啟 天堂交易分析_最新.xlsx
```
