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


def _copy_vendor_reports() -> list[tuple[str, str]]:
  links: list[tuple[str, str]] = []
  for vendor in list_publish_vendors():
    src = REPORTS_DIR / vendor
    if not src.exists():
      continue

    dst = OUT_DIR / vendor
    shutil.copytree(src, dst)
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

    cards = []
    for name, link in vendor_links:
        cards.append(
            f"<a class='card' href='{link}'><h2>{name}</h2><p>查看最新儀表板與分類報告</p></a>",
        )
    cards_html = "\n".join(cards) if cards else "<p class='empty'>目前尚未產生可發布報告。</p>"

    if update_items:
        update_html = "".join(
            (
                "<li>"
          "<div class='timeline-head'>"
          f"<strong>{title}</strong>"
          f"<span>{date_text}</span>"
          "</div>"
                f"<p>{summary if summary else '（尚未填寫重點摘要）'}</p>"
                "</li>"
            )
            for title, date_text, summary in update_items
        )
    else:
      update_html = "<li><div class='timeline-head'><strong>尚無版本紀錄</strong><span>請先建立 docs/ui-version-history.md</span></div><p>（尚未填寫重點摘要）</p></li>"

    html = f"""<!DOCTYPE html>
<html lang="zh-TW">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>超級機器人大戰系列看板</title>
  <style>
    :root {{
      --bg: #f3f7fa;
      --panel: #ffffff;
      --text: #1f2f3a;
      --muted: #607785;
      --brand: #1f7a6c;
      --brand2: #255f88;
      --line: #d6e1e8;
      --shadow: 0 12px 30px rgba(20, 45, 60, 0.08);
    }}
    * {{ box-sizing: border-box; }}
    body {{
      margin: 0;
      font-family: "Noto Sans TC", "Microsoft JhengHei", sans-serif;
      background:
        radial-gradient(circle at 8% 8%, #d9ede8 0%, transparent 42%),
        radial-gradient(circle at 90% 6%, #d8e8f5 0%, transparent 40%),
        var(--bg);
      color: var(--text);
      padding: 20px 14px 28px;
    }}
    .shell {{
      max-width: 1080px;
      margin: 0 auto;
      background: var(--panel);
      border: 1px solid var(--line);
      border-radius: 16px;
      box-shadow: var(--shadow);
      overflow: hidden;
    }}
    .hero {{
      padding: 22px 24px;
      color: #fff;
      background: linear-gradient(120deg, var(--brand), var(--brand2));
    }}
    .hero h1 {{ margin: 0 0 6px; font-size: 1.8rem; }}
    .hero p {{ margin: 0; opacity: 0.92; }}
    .content {{ padding: 16px 18px 22px; }}
    .grid {{
      display: grid;
      grid-template-columns: repeat(3, minmax(0, 1fr));
      gap: 10px;
      margin-bottom: 14px;
    }}
    .card {{
      display: block;
      text-decoration: none;
      color: var(--text);
      border: 1px solid var(--line);
      border-radius: 12px;
      background: #f9fcff;
      padding: 12px;
    }}
    .card:hover {{ background: #eef6fb; }}
    .card h2 {{ margin: 0 0 4px; font-size: 1.2rem; color: #174556; }}
    .card p {{ margin: 0; color: var(--muted); font-size: 0.92rem; }}
    .empty {{
      border: 1px dashed var(--line);
      border-radius: 12px;
      padding: 12px;
      color: var(--muted);
      background: #f8fbfd;
    }}
    .footer {{
      margin-top: 8px;
      display: flex;
      flex-wrap: wrap;
      gap: 12px;
      color: var(--muted);
      font-size: 0.9rem;
    }}
    .panels {{
      display: grid;
      grid-template-columns: 1fr 1fr;
      gap: 10px;
      margin-top: 10px;
    }}
    .panel {{
      border: 1px solid var(--line);
      border-radius: 12px;
      background: #f9fcff;
      padding: 12px;
    }}
    .panel h3 {{ margin: 0 0 8px; color: #174556; }}
    .timeline {{ list-style: none; margin: 0; padding: 0; }}
    .timeline li {{
      border-bottom: 1px solid #e5edf3;
      padding: 7px 0;
      font-size: 0.9rem;
    }}
    .timeline-head {{
      display: inline-flex;
      align-items: baseline;
      gap: 8px;
      flex-wrap: wrap;
    }}
    .timeline li:last-child {{ border-bottom: 0; }}
    .timeline span {{ color: var(--muted); white-space: nowrap; }}
    .timeline p {{
      margin: 4px 0 0;
      color: #36515f;
      font-size: 0.88rem;
    }}
    .footer a, .panel a {{ color: #1d5a85; text-decoration: none; }}
    .footer a:hover, .panel a:hover {{ text-decoration: underline; }}
    @media (max-width: 860px) {{
      .grid {{ grid-template-columns: repeat(2, minmax(0, 1fr)); }}
      .panels {{ grid-template-columns: 1fr; }}
    }}
    @media (max-width: 560px) {{
      .grid {{ grid-template-columns: 1fr; }}
      .hero h1 {{ font-size: 1.4rem; }}
    }}
  </style>
</head>
<body>
  <main class="shell">
    <header class="hero">
      <h1>超級機器人大戰系列看板</h1>
      <p>更新時間：{generated_at}</p>
    </header>
    <section class="content">
      <div class="grid">
        {cards_html}
      </div>
      <div class="footer">
        <span>法務與風險：</span>
        <a href="./legal/disclaimer.html">免責聲明</a>
        <a href="./legal/privacy.html">隱私與資料說明</a>
        <a href="./legal/risk-disclosure.html">風險揭露</a>
      </div>
      <div class="panels">
        <section class="panel">
          <h3>更新紀錄</h3>
          <ul class="timeline">{update_html}</ul>
        </section>
        <section class="panel">
          <h3>站務聯絡</h3>
          <p>內容修正、下架請求、合作提案請來信：</p>
          <p>{contact_html}</p>
          <p>建議回報時附上頁面網址與問題描述，方便快速處理。</p>
        </section>
      </div>
    </section>
  </main>
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
