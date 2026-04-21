from __future__ import annotations

import argparse
import re
import sys
import urllib.request
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import unquote

ROOT = Path(__file__).resolve().parents[1]
SITE_DIR = ROOT / "site_publish"


class AnchorParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.hrefs: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag.lower() != "a":
            return
        attr_map = dict(attrs)
        href = attr_map.get("href")
        if href:
            self.hrefs.append(href)


def _iter_html_files(base: Path) -> list[Path]:
    return sorted(base.rglob("*.html"))


def _is_external_or_skip(href: str) -> bool:
    lowered = href.lower()
    return (
        lowered.startswith("http://")
        or lowered.startswith("https://")
        or lowered.startswith("mailto:")
        or lowered.startswith("tel:")
        or lowered.startswith("javascript:")
        or lowered.startswith("#")
    )


def _resolve_target(source_file: Path, href: str) -> Path:
    clean = unquote(href.split("#", 1)[0].split("?", 1)[0]).strip()
    if clean.startswith("/"):
        target = SITE_DIR / clean.lstrip("/")
    else:
        target = (source_file.parent / clean).resolve()

    if target.is_dir():
        target = target / "index.html"
    return target


def validate_site_links() -> tuple[list[str], list[str]]:
    if not SITE_DIR.exists():
        return ([f"site directory not found: {SITE_DIR}"], [])

    broken: list[str] = []
    policy_issues: list[str] = []

    if (SITE_DIR / "交易群").exists():
        policy_issues.append("disallowed directory exists: site_publish/交易群")

    for html_file in _iter_html_files(SITE_DIR):
        parser = AnchorParser()
        parser.feed(html_file.read_text(encoding="utf-8", errors="ignore"))

        for href in parser.hrefs:
            if "交易群" in href:
                policy_issues.append(
                    f"disallowed vendor link found in {html_file.relative_to(SITE_DIR)} -> {href}",
                )

            if _is_external_or_skip(href):
                continue

            target = _resolve_target(html_file, href)
            if not target.exists():
                broken.append(
                    f"broken link: {html_file.relative_to(SITE_DIR)} -> {href}",
                )

    return broken, policy_issues


# ---------------------------------------------------------------------------
# Additional content checks
# ---------------------------------------------------------------------------

# LINE IDs look like @[6+ alphanumeric chars]; @media is a CSS false positive
_LINE_ID_RE = re.compile(r"@[a-zA-Z0-9]{6,}")

# Categories that must NOT appear in site_publish
EXCLUDED_CATEGORIES = {"06_買賣交易"}

# Vendor directories that must NOT appear in site_publish
EXCLUDED_VENDORS = {"交易群"}

# Pages that must contain a notice-bar disclaimer
REPORT_SUBDIRS = {"機器熊", "特工"}

# Required elements per page type
_NOTICE_BAR_MARKER = 'class="notice-bar"'
_LEGAL_DISCLAIMER_LINK_RE = re.compile(r'href="[^"]*legal/disclaimer\.html"')


def check_content(site_dir: Path) -> list[str]:
    """Return list of content policy issues found in site_publish HTML files."""
    issues: list[str] = []

    for html_file in _iter_html_files(site_dir):
        rel = html_file.relative_to(site_dir)
        rel_str = str(rel)
        text = html_file.read_text(encoding="utf-8", errors="ignore")

        # 1. No LINE IDs (@xxxxxxx) — skip @media (CSS)
        for match in _LINE_ID_RE.finditer(text):
            token = match.group(0)
            # heuristic: @media is always followed by a space or ( in CSS
            surrounding = text[match.start() : match.start() + 20]
            if surrounding.startswith("@media"):
                continue
            issues.append(f"LINE ID leak: {rel_str} contains '{token}'")

        # 2. Excluded vendor directories must not be referenced
        for vendor in EXCLUDED_VENDORS:
            if vendor in text:
                issues.append(f"excluded vendor ref: {rel_str} mentions '{vendor}'")

        # 3. Excluded category links must not appear
        for cat in EXCLUDED_CATEGORIES:
            if f'href="{cat}.html"' in text:
                issues.append(f"excluded category link: {rel_str} links to {cat}.html")

        # 4. Every vendor report page should have notice-bar
        parts = rel.parts
        if len(parts) == 2 and parts[0] in REPORT_SUBDIRS and parts[1].endswith(".html"):
            if _NOTICE_BAR_MARKER not in text:
                issues.append(f"missing notice-bar: {rel_str}")
            if not _LEGAL_DISCLAIMER_LINK_RE.search(text):
                issues.append(f"missing disclaimer link: {rel_str}")

    return issues


def main() -> None:
    parser = argparse.ArgumentParser(description="Validate site_publish or live Pages site.")
    parser.add_argument(
        "--live",
        metavar="BASE_URL",
        help="Also fetch and validate the live Pages site at BASE_URL (e.g. https://rabbit99.github.io/buytool-report-hub)",
    )
    args = parser.parse_args()

    broken, policy_issues = validate_site_links()
    content_issues = check_content(SITE_DIR)
    live_issues: list[str] = []

    if args.live:
        live_issues = validate_live_site(args.live.rstrip("/"))

    all_issues = [*broken, *policy_issues, *content_issues, *live_issues]

    if broken:
        print("[ERROR] Broken links detected:")
        for item in broken:
            print(f"- {item}")

    if policy_issues:
        print("[ERROR] Publish policy issues detected:")
        for item in policy_issues:
            print(f"- {item}")

    if content_issues:
        print("[ERROR] Content policy issues detected:")
        for item in content_issues:
            print(f"- {item}")

    if live_issues:
        print("[ERROR] Live site issues detected:")
        for item in live_issues:
            print(f"- {item}")

    if all_issues:
        raise SystemExit(1)

    counts = {"html": len(list(_iter_html_files(SITE_DIR)))}
    live_note = f" + live site validated" if args.live else ""
    print(f"[OK] Site validation passed — {counts['html']} HTML pages checked{live_note}, no issues.")


# ---------------------------------------------------------------------------
# Live site validation
# ---------------------------------------------------------------------------

# Pages to spot-check on the live site: (url_path, checks)
# checks is a list of strings that MUST appear in the fetched HTML
_LIVE_SPOT_CHECKS: list[tuple[str, list[str]]] = [
    ("/%E7%89%B9%E5%B7%A5/01_%E6%8E%9B%E6%A9%9F%E6%94%BB%E7%95%A5.html",  ["ai-badge", "AI \u60c5\u5831\u6574\u7406"]),
    ("/%E7%89%B9%E5%B7%A5/00_%E7%B8%BD%E8%A6%BD.html",                     ["notice-bar"]),
    ("/%E6%A9%9F%E5%99%A8%E7%86%8A/01_%E6%8E%9B%E6%A9%9F%E6%94%BB%E7%95%A5.html", ["ai-badge", "AI \u60c5\u5831\u6574\u7406"]),
    ("/%E6%A9%9F%E5%99%A8%E7%86%8A/00_%E7%B8%BD%E8%A6%BD.html",            ["notice-bar"]),
    ("/index.html",                                                          ["vendor-card"]),
    ("/legal/disclaimer.html",                                               ["\u4e0d\u9f13\u52f5"]),
]

# Strings that must NOT appear anywhere in live pages
_LIVE_FORBIDDEN: list[str] = ["@622tofsr", "@167cwdek", "06_買賣交易.html", "交易群"]


def _fetch_url(url: str, timeout: int = 15) -> str | None:
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "buytool-site-checker/1.0"})
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.read().decode("utf-8", errors="ignore")
    except Exception as exc:
        return None


def validate_live_site(base_url: str) -> list[str]:
    """Fetch key pages from the live site and check for required/forbidden content."""
    issues: list[str] = []

    for path, required_tokens in _LIVE_SPOT_CHECKS:
        url = base_url + path
        html = _fetch_url(url)
        if html is None:
            issues.append(f"live: failed to fetch {url}")
            continue

        for token in required_tokens:
            if token not in html:
                issues.append(f"live: '{token}' missing in {path}")

        for forbidden in _LIVE_FORBIDDEN:
            if forbidden in html:
                issues.append(f"live: forbidden content '{forbidden}' found in {path}")

    return issues


if __name__ == "__main__":
    main()
