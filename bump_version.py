#!/usr/bin/env python3
"""Automate version bumps for RetroStation MC."""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
from datetime import datetime
from pathlib import Path

README_FILE = Path("README.md")
CHANGELOG_FILE = Path("CHANGELOG.md")
INDEX_TEMPLATE_FILE = Path("app/templates/index.html")
ADMIN_ABOUT_TEST_FILE = Path("tests/test_admin_about_section.py")


def normalize_version(raw: str) -> tuple[str, str]:
    base = raw.strip().lstrip("vV")
    if not re.fullmatch(r"\d+\.\d+\.\d+", base):
        raise SystemExit(f"Invalid version '{raw}'. Expected format: 1.1.0 or v1.1.0")
    return base, f"v{base}"


def update_readme(v_version: str) -> None:
    if not README_FILE.exists():
        print(f"[-] {README_FILE} not found, skipping")
        return

    text = README_FILE.read_text(encoding="utf-8")
    text, badge_count = re.subn(
        r"(version-)v\d+\.\d+\.\d+(-blue)",
        rf"\1{v_version}\2",
        text,
    )
    text, line_count = re.subn(
        r"(This repository is versioned as \*\*)v\d+\.\d+\.\d+(\*\*\.)",
        rf"\1{v_version}\2",
        text,
    )
    README_FILE.write_text(text, encoding="utf-8")
    print(f"[+] README.md: updated badge ({badge_count}) and repository version line ({line_count})")


def update_index_template(v_version: str) -> None:
    if not INDEX_TEMPLATE_FILE.exists():
        print(f"[-] {INDEX_TEMPLATE_FILE} not found, skipping")
        return

    text = INDEX_TEMPLATE_FILE.read_text(encoding="utf-8")
    new_text, count = re.subn(
        r"(<span class=\"info-label\">Version</span><code>)v\d+\.\d+\.\d+(</code>)",
        rf"\1{v_version}\2",
        text,
    )
    INDEX_TEMPLATE_FILE.write_text(new_text, encoding="utf-8")
    print(f"[+] app/templates/index.html: updated About tab version ({count})")


def update_admin_about_test(v_version: str) -> None:
    if not ADMIN_ABOUT_TEST_FILE.exists():
        print(f"[-] {ADMIN_ABOUT_TEST_FILE} not found, skipping")
        return

    text = ADMIN_ABOUT_TEST_FILE.read_text(encoding="utf-8")
    escaped_v_version = v_version.replace(".", r"\.")
    new_text, count = re.subn(
        r"(RetroStation MC\[\\s\\S]\*\?)(v\d+\\\.\d+\\\.\d+)(\[\\s\\S]\*\?RetroIPTVGuide)",
        rf"\1{escaped_v_version}\3",
        text,
    )
    ADMIN_ABOUT_TEST_FILE.write_text(new_text, encoding="utf-8")
    print(f"[+] tests/test_admin_about_section.py: updated About tab version expectation ({count})")


def update_changelog(v_version: str, date_str: str) -> None:
    if not CHANGELOG_FILE.exists():
        print(f"[-] {CHANGELOG_FILE} not found, skipping")
        return

    content = CHANGELOG_FILE.read_text(encoding="utf-8")
    if f"## [{v_version}] - " in content:
        print(f"[=] CHANGELOG.md: section for {v_version} already exists, skipping")
        return

    lines = content.splitlines()
    try:
        separator_index = next(i for i, line in enumerate(lines) if line.strip() == "---")
    except StopIteration as exc:
        raise SystemExit("Could not find top '---' separator in CHANGELOG.md") from exc

    new_block = [
        "",
        f"## [{v_version}] - {date_str}",
        "",
        "### Added",
        "- (empty)",
        "",
        "### Changed",
        "- (empty)",
        "",
        "### Fixed",
        "- (empty)",
        "",
        "### Security",
        "- (empty)",
        "",
        "### Known Issues",
        "- (empty)",
        "",
    ]

    updated = lines[: separator_index + 1] + new_block + lines[separator_index + 1 :]
    CHANGELOG_FILE.write_text("\n".join(updated) + "\n", encoding="utf-8")
    print(f"[+] CHANGELOG.md: inserted section for {v_version} - {date_str}")


def git_commit(v_version: str) -> None:
    tracked_files = [
        str(path)
        for path in [README_FILE, CHANGELOG_FILE, INDEX_TEMPLATE_FILE, ADMIN_ABOUT_TEST_FILE]
        if path.exists()
    ]
    if not tracked_files:
        print("[!] No files to add to git")
        return

    try:
        subprocess.run(["git", "add", *tracked_files], check=True)
        subprocess.run(["git", "commit", "-m", f"Bump version to {v_version}"], check=True)
        print("[+] Git commit created")
    except subprocess.CalledProcessError:
        print("[!] Git commit failed")


def main(argv: list[str]) -> None:
    parser = argparse.ArgumentParser(description="Bump RetroStation MC version strings.")
    parser.add_argument("new_version", help="Version to set, e.g. 1.1.0 or v1.1.0")
    parser.add_argument("--date", dest="release_date", help="Release date in YYYY-MM-DD format")
    parser.add_argument("--commit", action="store_true", help="Create a git commit after updates")
    args = parser.parse_args(argv[1:])

    _, v_version = normalize_version(args.new_version)
    release_date = args.release_date or datetime.today().strftime("%Y-%m-%d")

    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", release_date):
        raise SystemExit("Invalid --date format. Expected YYYY-MM-DD")

    print("== RetroStation MC version bump ==")
    print(f"   New version: {v_version}")
    print(f"   Release date: {release_date}")
    print("")

    update_readme(v_version)
    update_index_template(v_version)
    update_admin_about_test(v_version)
    update_changelog(v_version, release_date)

    if args.commit:
        git_commit(v_version)


if __name__ == "__main__":
    main(sys.argv)
