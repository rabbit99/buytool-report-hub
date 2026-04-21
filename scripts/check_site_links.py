from __future__ import annotations

import re
import sys
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
    broken, policy_issues = validate_site_links()
    content_issues = check_content(SITE_DIR)

    all_issues = [*broken, *policy_issues, *content_issues]

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

    if all_issues:
        raise SystemExit(1)

    counts = {
        "html": len(list(_iter_html_files(SITE_DIR))),
    }
    print(f"[OK] Site validation passed — {counts['html']} HTML pages checked, no issues.")


if __name__ == "__main__":
    main()
