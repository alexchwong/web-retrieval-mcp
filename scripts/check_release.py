#!/usr/bin/env python3
"""Release checks for the webSpell wheel (Section 12).

Usage: python scripts/check_release.py dist/webspell-X.Y.Z-py3-none-any.whl

Checks that release/VERSION, webspell/_version.py and the wheel agree; NEWS.md has
a section for the version; the wheel contains exactly the webspell package and the
licence (no tests, testdata, settings file, Docker or SearXNG files); and the
metadata has the requirement groups of decision D4 and ``License-Expression: MIT``.
"""
from __future__ import annotations

from email.parser import Parser
from pathlib import Path
import re
import sys
import zipfile

ROOT = Path(__file__).resolve().parents[1]

CORE = {
    'httpx<1,>=0.28.1',
    'trafilatura<3,>=2.0.0',
    'tomli>=2.0.1; python_version < "3.11"',
}
EXTRAS = {
    "pdf": {"pypdfium2<6,>=5"},
    "server": {"fastmcp<5,>=4.1.6"},
    # Test-only references (rank-bm25 for ranking parity, jsonschema for the validator).
    "dev": {"rank-bm25==0.2.2", "jsonschema>=4"},
}
MODULES = {"__init__", "__main__", "_version", "api", "extract", "limit", "limits", "outbound",
           "rank", "search", "server", "settings", "validate"}


def fail(message: str) -> None:
    raise SystemExit(f"release check failed: {message}")


def normalise(requirement: str) -> tuple[str, str | None]:
    """Split 'name<spec>; marker' into (requirement without extra marker, extra)."""
    requirement = requirement.strip()
    extra = None
    match = re.search(r"""\s*;?\s*(?:and\s+)?extra\s*==\s*["']([^"']+)["']""", requirement)
    if match:
        extra = match.group(1)
        requirement = (requirement[:match.start()] + requirement[match.end():]).strip().rstrip(";").strip()
    name, _, rest = requirement.partition(";")
    name = re.sub(r"\s+", "", name)
    marker = rest.strip().replace("'", '"')
    return (f"{name}; {marker}" if marker else name), extra


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        print(__doc__, file=sys.stderr)
        return 2
    wheel = Path(argv[1])
    version = (ROOT / "release" / "VERSION").read_text().strip()
    source_version = re.search(r'__version__ = "([^"]+)"', (ROOT / "webspell" / "_version.py").read_text())
    if not source_version or source_version.group(1) != version:
        fail(f"webspell/_version.py does not match release/VERSION {version}")
    if wheel.name != f"webspell-{version}-py3-none-any.whl":
        fail(f"unexpected wheel name {wheel.name}")
    if not re.search(rf"^## {re.escape(version)}\s*$", (ROOT / "NEWS.md").read_text(), re.M):
        fail(f"NEWS.md has no '## {version}' section")

    dist_info = f"webspell-{version}.dist-info"
    with zipfile.ZipFile(wheel) as archive:
        names = set(archive.namelist())
        metadata = Parser().parsestr(archive.read(f"{dist_info}/METADATA").decode())
    package = {name for name in names if name.startswith("webspell/")}
    expected_package = {f"webspell/{module}.py" for module in MODULES}
    if package != expected_package:
        fail(f"package members differ: missing {sorted(expected_package - package)}, "
             f"extra {sorted(package - expected_package)}")
    info = names - package
    expected_info = {f"{dist_info}/{name}" for name in ("METADATA", "WHEEL", "RECORD", "licenses/LICENSE")}
    if info != expected_info:
        fail(f"unexpected wheel members: {sorted(info ^ expected_info)}")
    for forbidden in ("test", "testdata", "settings.toml", "Dockerfile", "searxng", ".pem", ".pdf"):
        if any(forbidden in name for name in names):
            fail(f"wheel contains {forbidden!r}")

    if metadata["Name"] != "webSpell" or metadata["Version"] != version:
        fail(f"metadata name/version are {metadata['Name']} {metadata['Version']}")
    if metadata["License-Expression"] != "MIT":
        fail(f"License-Expression is {metadata['License-Expression']!r}")
    if metadata["Requires-Python"] != ">=3.10":
        fail(f"Requires-Python is {metadata['Requires-Python']!r}")
    groups: dict[str | None, set[str]] = {}
    for requirement in metadata.get_all("Requires-Dist") or []:
        text, extra = normalise(requirement)
        groups.setdefault(extra, set()).add(text)
    if groups.get(None, set()) != CORE:
        fail(f"core requirements are {sorted(groups.get(None, set()))}")
    for extra, requirements in EXTRAS.items():
        if groups.get(extra, set()) != requirements:
            fail(f"extra {extra!r} requirements are {sorted(groups.get(extra, set()))}")
    extras = set(metadata.get_all("Provides-Extra") or [])
    if extras != set(EXTRAS) or set(groups) - {None} != set(EXTRAS):
        fail(f"extras are {sorted(extras)}")
    print(f"PASS: {wheel.name}: {len(package)} modules, licence MIT, requirement groups as D4")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
