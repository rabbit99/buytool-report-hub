"""Build publish-ready static site for GitHub Pages.

This script collects generated HTML reports and combines them with static legal/risk pages
into site_publish for deployment.
"""

from __future__ import annotations

import os
import re
import shutil
from datetime import datetime, timezone
from pathlib import Path

from vendor_config import list_publish_vendors

ROOT = Path(__file__).resolve().parent
REPORTS_DIR = ROOT / "reports"
STATIC_DIR = ROOT / "site_static"
OUT_DIR = ROOT / "site_publish"
UI_HISTORY_PATH = ROOT / "docs" / "ui-version-history.md"


def _clean_output() -> None:
    if OUT_DIR.exists():
        shutil.rmtree(OUT_DIR)
    OUT_DIR.mkdir(parents=True, exist_ok=True)


PUBLISH_EXCLUDED_CATEGORIES: set[str] = {"06_買賣交易"}


def _build_excluded_link_pattern() -> re.Pattern[str]:
    alts = "|".join(re.escape(c) + r"\.html" for c in PUBLISH_EXCLUDED_CATEGORIES)
    return re.compile(r'<a\b[^>]*\bhref="(?:' + alts + r')"[^>]*>.*?</a>', re.DOTALL)

_EXCLUDED_LINK_RE: re.Pattern[str] = _build_excluded_link_pattern()


def _strip_excluded_links(html: str) -> str:
    """Remove anchor tags pointing to excluded category files from HTML."""
    return _EXCLUDED_LINK_RE.sub("", html)


def _post_process_vendor_html(vendor_dst: Path) -> None:
    """Sanitize all HTML files in a vendor publish directory."""
    for html_file in vendor_dst.rglob("*.html"):
        original = html_file.read_text(encoding="utf-8")
        cleaned = _strip_excluded_links(original)
        if cleaned != original:
            html_file.write_text(cleaned, encoding="utf-8")


def _copy_vendor_reports() -> list[tuple[str, str]]:
  links: list[tuple[str, str]] = []
  for vendor in list_publish_vendors():
    src = REPORTS_DIR / vendor
    if not src.exists():
      continue

    dst = OUT_DIR / vendor
    # Copy all files except excluded categories
    dst.mkdir(parents=True, exist_ok=True)
    for item in src.iterdir():
      # Skip excluded category HTML/MD files
      skip = any(item.stem == cat for cat in PUBLISH_EXCLUDED_CATEGORIES)
      if skip:
        continue
      if item.is_dir():
        shutil.copytree(item, dst / item.name)
      else:
        shutil.copy2(item, dst / item.name)
    _post_process_vendor_html(dst)
    if (dst / "00_總覽.html").exists():
      links.append((vendor, f"./{vendor}/00_總覽.html"))
  return links


def _copy_static_assets() -> None:
    if not STATIC_DIR.exists():
        return
    for item in STATIC_DIR.iterdir():
        target = OUT_DIR / item.name
        if item.is_dir():
            shutil.copytree(item, target)
        else:
            shutil.copy2(item, target)


def _load_update_history(max_items: int = 5) -> list[tuple[str, str, str]]:
    """Read compact version timeline from ui-version-history markdown."""
    if not UI_HISTORY_PATH.exists():
        return []

    lines = UI_HISTORY_PATH.read_text(encoding="utf-8").splitlines()
    entries: list[tuple[str, str, str]] = []

    for idx, raw in enumerate(lines):
        line = raw.strip()
        if not line.startswith("## "):
            continue

        title = line[3:].strip()
        date_match = re.search(r"\(([^)]+)\)", title)
        date_text = date_match.group(1) if date_match else ""
        clean_title = re.sub(r"\s*\([^)]+\)", "", title).strip()

        version_match = re.search(r"v\d+\.\d+\.\d+", clean_title)
        display_version = version_match.group(0) if version_match else clean_title

        summary = ""
        for next_line in lines[idx + 1 :]:
            next_line = next_line.strip()
            if next_line.startswith("## "):
                break
            if next_line.startswith("- "):
                summary = next_line[2:].strip().rstrip(".")
                break

        entries.append((display_version, date_text, summary))
        if len(entries) >= max_items:
            break
    return entries


def _build_robots_content(base_url: str) -> str:
    return (
        "User-agent: *\n"
        "Allow: /\n\n"
        f"Sitemap: {base_url.rstrip('/')}/sitemap.xml\n"
    )


def _build_sitemap_content(base_url: str, paths: list[str]) -> str:
    now_iso = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    body = []
    for path in paths:
        loc = f"{base_url.rstrip('/')}/{path.lstrip('./')}"
        body.append(
            "<url>"
            f"<loc>{loc}</loc>"
            f"<lastmod>{now_iso}</lastmod>"
            "</url>",
        )
    return (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">'
        + "".join(body)
        + "</urlset>"
    )


def _write_index(vendor_links: list[tuple[str, str]]) -> None:
    generated_at = datetime.now().strftime("%Y-%m-%d")
    contact_email = os.getenv("PUBLIC_CONTACT_EMAIL", "未設定，請於 Repository Variables 設定 PUBLIC_CONTACT_EMAIL")
    contact_html = (
        f'<a href="mailto:{contact_email}">{contact_email}</a>'
        if "@" in contact_email
        else contact_email
    )
    update_items = _load_update_history()

    # Sidebar vendor links
    sidebar_vendor_links = "".join(
        f'<a class="vendor-link" href="{link}">{name}</a>'
        for name, link in vendor_links
    ) if vendor_links else '<span class="vendor-link" style="opacity:.5">尚無廠商</span>'

    # Main vendor cards
    if vendor_links:
        cards_html = "".join(
            f'<a class="vendor-card" href="{link}">'
            f'<div class="vendor-card-header"><div class="vendor-card-name">{name}</div>'
            f'<div class="vendor-card-sub">查看儀表板與分類報告</div></div>'
            f'<div class="vendor-card-arrow">→</div>'
            f'</a>'
            for name, link in vendor_links
        )
    else:
        cards_html = "<p class='empty'>目前尚未產生可發布報告。</p>"

    # Update history
    if update_items:
        update_html = "".join(
            f'<li class="tl-item">'
            f'<span class="tl-time">{date_text}</span>'
            f'<div class="tl-body"><strong>{title}</strong>'
            f'{"<div class=tl-sub>" + summary + "</div>" if summary else ""}'
            f'</div></li>'
            for title, date_text, summary in update_items
        )
    else:
        update_html = '<li class="tl-item"><span class="tl-time">—</span><div class="tl-body">尚無版本紀錄</div></li>'

    html = f"""<!DOCTYPE html>
<html lang="zh-TW">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>超級機器人大戰系列看板</title>
<style>
:root{{
  --sidebar-bg:#13303a;--sidebar-hover:#1e4655;--sidebar-active:#2a7f6c;
  --brand:#2a7f6c;--brand-2:#1e5f88;
  --brand-grad:linear-gradient(120deg,var(--brand) 0%,var(--brand-2) 100%);
  --page-bg:#f1f5f8;--card-bg:#fff;--card-border:#d7e3e8;
  --card-shadow:0 2px 12px rgba(15,35,45,.07);
  --card-shadow-hover:0 6px 24px rgba(15,35,45,.13);
  --text:#1a2b34;--text-sub:#4d6168;--radius:16px;--radius-sm:10px;
}}
*{{box-sizing:border-box;margin:0;padding:0}}
body{{font-family:"Noto Sans TC","Microsoft JhengHei",sans-serif;color:var(--text);background:var(--page-bg);line-height:1.7}}
.layout{{display:grid;grid-template-columns:240px 1fr;min-height:100vh}}
.sidebar{{background:var(--sidebar-bg);color:#c8dde5;position:sticky;top:0;height:100vh;overflow-y:auto;display:flex;flex-direction:column}}
.sidebar-top{{padding:18px 16px 12px;border-bottom:1px solid rgba(255,255,255,.08)}}
.sidebar-site{{font-size:1.05rem;font-weight:700;color:#e8f4f8;margin-bottom:2px}}
.sidebar-sub{{font-size:.82rem;color:#80b0bd}}
.sidebar-section-label{{font-size:.72rem;letter-spacing:.08em;text-transform:uppercase;color:#6090a0;padding:12px 16px 4px}}
.vendor-link{{display:block;padding:7px 16px;color:#a8c8d5;text-decoration:none;font-size:.9rem;border-left:3px solid transparent;transition:all .15s ease}}
.vendor-link:hover{{background:var(--sidebar-hover);color:#e8f4f8;border-left-color:var(--brand)}}
.legal-link{{display:block;padding:5px 16px;color:#6a9aaa;text-decoration:none;font-size:.82rem;transition:color .12s}}
.legal-link:hover{{color:#c8dde5}}
.sidebar-footer{{margin-top:auto;padding:12px 16px;border-top:1px solid rgba(255,255,255,.08);font-size:.78rem;color:#5a8090}}
.main{{padding:20px 22px 32px;max-width:900px}}
.topbar{{display:flex;align-items:center;justify-content:space-between;flex-wrap:wrap;gap:8px;margin-bottom:16px}}
.topbar-title{{font-size:1.35rem;font-weight:700;color:var(--text)}}
.topbar-date{{font-size:.85rem;color:var(--text-sub)}}
.vendor-grid{{display:grid;grid-template-columns:repeat(2,1fr);gap:12px;margin-bottom:16px}}
.vendor-card{{background:var(--card-bg);border:1px solid var(--card-border);border-radius:var(--radius);box-shadow:var(--card-shadow);overflow:hidden;transition:box-shadow .2s ease;text-decoration:none;color:var(--text);display:flex;align-items:stretch}}
.vendor-card:hover{{box-shadow:var(--card-shadow-hover)}}
.vendor-card-header{{flex:1;padding:14px 16px;background:var(--brand-grad);color:#fff}}
.vendor-card-name{{font-size:1.15rem;font-weight:700}}
.vendor-card-sub{{font-size:.83rem;opacity:.88;margin-top:3px}}
.vendor-card-arrow{{display:flex;align-items:center;padding:0 16px;background:rgba(42,127,108,.08);color:var(--brand);font-size:1.3rem;font-weight:300}}
.vendor-card:hover .vendor-card-arrow{{background:rgba(42,127,108,.16)}}
.empty{{border:1px dashed var(--card-border);border-radius:var(--radius-sm);padding:14px;color:var(--text-sub);background:#f8fbfd}}
.panels{{display:grid;grid-template-columns:1fr 1fr;gap:12px}}
.panel{{background:var(--card-bg);border:1px solid var(--card-border);border-radius:var(--radius);box-shadow:var(--card-shadow);padding:14px 16px}}
.panel-title{{font-size:.95rem;font-weight:700;color:#1b4b5a;margin-bottom:10px;padding-bottom:6px;border-bottom:1px solid var(--card-border)}}
.panel p{{font-size:.9rem;color:var(--text-sub);margin-bottom:6px}}
.panel a{{color:#1d5a85;text-decoration:none}}
.panel a:hover{{text-decoration:underline}}
.tl-list{{list-style:none;display:grid;gap:7px}}
.tl-item{{border:1px solid #dfe9ee;background:#f9fcfe;border-radius:var(--radius-sm);padding:8px 11px}}
.tl-time{{display:block;color:#40616f;font-size:.78rem;margin-bottom:2px}}
.tl-body{{font-size:.88rem}}
.tl-sub{{color:#5f7680;font-size:.82rem;margin-top:2px}}
.report-footer{{margin-top:12px;font-size:.82rem;color:#7a909a;text-align:center;padding:12px 0 0;border-top:1px solid var(--card-border)}}
@media(max-width:780px){{
  .layout{{grid-template-columns:1fr}}
  .sidebar{{position:static;height:auto}}
  .main{{padding:14px 12px 24px}}
  .vendor-grid{{grid-template-columns:1fr}}
  .panels{{grid-template-columns:1fr}}
}}
</style>
</head>
<body>
<div class="layout">
  <aside class="sidebar">
    <div class="sidebar-top">
      <div class="sidebar-site">系列看板</div>
      <div class="sidebar-sub">超級機器人大戰</div>
    </div>
    <div class="sidebar-section-label">選擇廠商</div>
    {sidebar_vendor_links}
    <div class="sidebar-section-label">法務與風險</div>
    <a class="legal-link" href="./legal/disclaimer.html">免責聲明</a>
    <a class="legal-link" href="./legal/privacy.html">隱私與資料說明</a>
    <a class="legal-link" href="./legal/risk-disclosure.html">風險揭露</a>
    <div class="sidebar-footer">更新：{generated_at}</div>
  </aside>
  <main class="main">
    <div class="topbar">
      <div class="topbar-title">報告總覽</div>
      <div class="topbar-date">資料更新：{generated_at}</div>
    </div>
    <div class="vendor-grid">
      {cards_html}
    </div>
    <div class="panels">
      <section class="panel">
        <div class="panel-title">更新紀錄</div>
        <ul class="tl-list">{update_html}</ul>
      </section>
      <section class="panel">
        <div class="panel-title">站務聯絡</div>
        <p>內容修正、下架請求、合作提案請來信：</p>
        <p>{contact_html}</p>
        <p>建議回報時附上頁面網址與問題描述，方便快速處理。</p>
      </section>
    </div>
    <footer class="report-footer">超級機器人大戰系列看板 ｜ 自動產生於 {generated_at}</footer>
  </main>
</div>
</body>
</html>
"""
    (OUT_DIR / "index.html").write_text(html, encoding="utf-8")


def _write_404() -> None:
    content = """<!DOCTYPE html>
<html lang="zh-TW">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>頁面不存在</title>
  <style>
    body { margin: 0; font-family: "Noto Sans TC", "Microsoft JhengHei", sans-serif; background: #f4f8fb; color: #1f2f3a; }
    main { max-width: 640px; margin: 12vh auto; background: #fff; border: 1px solid #d9e3ea; border-radius: 14px; padding: 20px; }
    a { color: #1f5f86; }
  </style>
</head>
<body>
  <main>
    <h1>找不到這個頁面</h1>
    <p>你要的內容可能已更新位置，請返回首頁重新選擇報告。</p>
    <p><a href="./index.html">回到報告首頁</a></p>
  </main>
</body>
</html>
"""
    (OUT_DIR / "404.html").write_text(content, encoding="utf-8")


def _write_robots_and_sitemap(vendor_links: list[tuple[str, str]]) -> None:
    base_url = os.getenv("SITE_BASE_URL", "https://example.github.io/BuyTool")
    paths = [
        "index.html",
        "404.html",
        "legal/disclaimer.html",
        "legal/privacy.html",
        "legal/risk-disclosure.html",
    ]
    for _vendor, link in vendor_links:
        paths.append(link)

    (OUT_DIR / "robots.txt").write_text(_build_robots_content(base_url), encoding="utf-8")
    (OUT_DIR / "sitemap.xml").write_text(_build_sitemap_content(base_url, paths), encoding="utf-8")


def main() -> None:
    _clean_output()
    links = _copy_vendor_reports()
    _copy_static_assets()
    _write_index(links)
    _write_404()
    _write_robots_and_sitemap(links)
    print(f"Site publish bundle prepared at: {OUT_DIR}")


if __name__ == "__main__":
    main()
