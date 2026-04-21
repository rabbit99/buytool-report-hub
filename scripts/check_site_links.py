from __future__ import annotations

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


def main() -> None:
    broken, policy_issues = validate_site_links()

    if broken:
        print("[ERROR] Broken links detected:")
        for item in broken:
            print(f"- {item}")

    if policy_issues:
        print("[ERROR] Publish policy issues detected:")
        for item in policy_issues:
            print(f"- {item}")

    if broken or policy_issues:
        raise SystemExit(1)

    print("[OK] Site validation passed: links and publish policy are clean.")


if __name__ == "__main__":
    main()
