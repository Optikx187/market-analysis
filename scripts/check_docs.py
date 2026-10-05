#!/usr/bin/env python3
from __future__ import annotations

import re
import sys
from pathlib import Path
from urllib.parse import unquote


ROOT = Path(__file__).resolve().parents[1]
MARKDOWN_LINK = re.compile(r"(?<!!)\[[^\]]+\]\(([^)]+)\)")


def markdown_files() -> list[Path]:
    files = [ROOT / "README.md"]
    files.extend((ROOT / "docs").rglob("*.md"))
    files.extend((ROOT / "deploy").rglob("*.md"))
    files.extend((ROOT / ".agents").rglob("*.md"))
    return sorted(path for path in files if path.is_file())


def missing_local_links(path: Path) -> list[str]:
    missing: list[str] = []
    for target in MARKDOWN_LINK.findall(path.read_text(encoding="utf-8")):
        target = target.strip().split(maxsplit=1)[0].strip("<>")
        if not target or target.startswith(("#", "http://", "https://", "mailto:")):
            continue
        relative_target = unquote(target.split("#", 1)[0])
        if relative_target and not (path.parent / relative_target).resolve().exists():
            missing.append(target)
    return missing


def main() -> int:
    failures = [
        f"{path.relative_to(ROOT)}: missing local link {target}"
        for path in markdown_files()
        for target in missing_local_links(path)
    ]
    if failures:
        print("\n".join(failures), file=sys.stderr)
        return 1
    print(f"Documentation links valid ({len(markdown_files())} files).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
