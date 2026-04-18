# Release Checklist

本清單用於 BuyTool 對外發布流程（master -> release -> GitHub Pages）。

## 0. 發布前準備

- [ ] 確認本機在 `master` 分支
- [ ] `git pull origin master` 同步最新程式碼
- [ ] `git status --short` 為乾淨狀態
- [ ] `.env` 不含任何要提交的敏感資訊
- [ ] 本次版本號與 tag 命名已確認（例如 `v0.5.1-xxx`）

## 1. 內容與報告驗證

- [ ] 執行不含 AI 報告流程：`npm run update`
- [ ] 如需 AI 版驗證：`npm run update:ai`
- [ ] 確認報告內容不含原始聊天句子（去識別規則有效）
- [ ] 抽查三個廠商的 `00_總覽.html` 可正常載入
- [ ] 抽查法務頁可正常載入：
  - [ ] `legal/disclaimer.html`
  - [ ] `legal/privacy.html`
  - [ ] `legal/risk-disclosure.html`

## 2. 建置與本機預覽

- [ ] 執行打包：`npm run site:build`
- [ ] 確認 `site_publish/` 產出成功
- [ ] 確認 `site_publish/index.html` 存在
- [ ] 確認 `site_publish/robots.txt` 與 `site_publish/sitemap.xml` 存在

## 3. Git 版本操作

- [ ] 提交變更：`git add ... && git commit -m "..."`
- [ ] 建立 tag：`git tag -a <tag> -m "..."`
- [ ] 推送 master：`git push origin master`
- [ ] 推送 tag：`git push origin <tag>`

## 4. 發布到 release（觸發 Pages）

擇一執行：

- [ ] 只推當前 HEAD 到 release：`npm run release:push`
- [ ] 先重跑不含 AI 後發布：`npm run release:mvp`
- [ ] 先重跑含 AI 後發布：`npm run release:mvp:ai`

## 5. GitHub Actions 驗證

- [ ] workflow `Deploy Reports To GitHub Pages` 已觸發
- [ ] `build` job 成功
- [ ] `deploy` job 成功
- [ ] 無 `requirements` 安裝錯誤
- [ ] 無 `ingest.py` / `gen_report.py` 執行錯誤

## 6. 線上站點驗證

- [ ] 首頁可開啟
- [ ] 廠商入口可開啟（特工 / 機器熊 / 交易群）
- [ ] 每個廠商至少一個分類頁可開啟
- [ ] 法務頁三頁可開啟
- [ ] `robots.txt` 可開啟
- [ ] `sitemap.xml` 可開啟

## 7. Repository Variables 檢查（必要）

GitHub Repo -> Settings -> Secrets and variables -> Actions -> Variables

- [ ] `SITE_BASE_URL` 已設定（例：`https://<user>.github.io/<repo>`）
- [ ] `PUBLIC_CONTACT_EMAIL` 已設定（站務聯絡信箱）

## 8. 發布後追蹤

- [ ] 在變更紀錄或公告中記錄本次 tag 與重點變更
- [ ] 更新 `docs/ui-version-history.md`（若涉及 UI）
- [ ] 記錄本次發布時間與負責人
- [ ] 若有回報問題，建立 hotfix 清單

---

## 快速命令參考

```powershell
# 1) 更新報告（不含 AI）
npm run update

# 2) 打包公開站
npm run site:build

# 3) 推到 release（觸發 GitHub Pages）
npm run release:push

# 4) 一條龍（更新 + 發布）
npm run release:mvp
```

## Hotfix 最短路徑

```powershell
# 修正後快速發布
npm run update
npm run release:push
```
