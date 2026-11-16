#!/usr/bin/env bash
# Regenerate the installed-size table (specification Section 4.1) on Linux for the
# current Python and write it into docs/operations.md between the
# measure_install markers.
#
# Usage: bash scripts/measure_install.sh dist/webspell-X.Y.Z-py3-none-any.whl
# Optional: AGENTWIZ_SPEC="<pip requirement for AgentWiz>" adds the AgentWiz row.
#
# Size is `du` of site-packages in a fresh virtual environment with pip (and, on
# Python < 3.12, setuptools) subtracted, rounded to the megabyte. Package counts
# exclude the same bootstrap packages and webSpell itself. Groups overlap, so rows
# do not add up.
# Docker and SearXNG image sizes are recorded separately, never here.
set -euo pipefail
cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.."

if [[ "$(uname -s)" != Linux ]]; then
    echo "measure_install.sh measures on Linux only" >&2
    exit 2
fi
if [[ $# -ne 1 || ! -f "$1" ]]; then
    echo "Usage: bash scripts/measure_install.sh dist/webspell-X.Y.Z-py3-none-any.whl" >&2
    exit 2
fi
wheel="$(cd -- "$(dirname -- "$1")" && pwd)/$(basename -- "$1")"
python="${PYTHON:-python3}"
work="$(mktemp -d)"
trap 'rm -rf "$work"' EXIT

# The web-retrieval-mcp 0.1.1 requirements.txt, for the baseline row.
cat > "$work/requirements-0.1.1.txt" <<'REQ'
fastmcp==4.1.6
tomli>=2.0.1; python_version < "3.11"
httpx[http2]>=0.28.1,<1
trafilatura>=2.0.0
rank-bm25>=0.2.2
lxml>=5.3.0
pymupdf>=1.24.0
REQ

measure() {  # measure LABEL NOTE PIP-ARGS...
    local label="$1" note="$2"; shift 2
    "$python" -m venv "$work/venv"
    "$work/venv/bin/python" -m pip install --quiet --disable-pip-version-check "$@"
    local site
    site="$("$work/venv/bin/python" -c 'import sysconfig; print(sysconfig.get_paths()["purelib"])')"
    local total bootstrap=0 path
    total="$(du -sk "$site" | cut -f1)"
    for path in "$site"/pip "$site"/pip-*.dist-info "$site"/setuptools "$site"/setuptools-*.dist-info \
                "$site"/_distutils_hack "$site"/pkg_resources; do
        [[ -e "$path" ]] && bootstrap=$((bootstrap + $(du -sk "$path" | cut -f1)))
    done
    local count
    count="$("$work/venv/bin/python" -m pip list --format=freeze --disable-pip-version-check \
             --exclude pip --exclude setuptools --exclude webSpell | wc -l | tr -d ' ')"
    printf '| %s | %d MB | %s | %s |\n' "$label" $(( (total - bootstrap + 512) / 1024 )) "$count" "$note" >> "$work/table.md"
    rm -rf "$work/venv"
}

pdfium_version="$("$python" -m pip index versions pypdfium2 2>/dev/null | sed -n 's/^pypdfium2 (\(.*\))$/\1/p')"
pymupdf_version="$("$python" -m pip index versions pymupdf 2>/dev/null | sed -n 's/^pymupdf (\(.*\))$/\1/p')"

{
    echo "Linux $(uname -m), Python $("$python" -c 'import platform; print(platform.python_version())'),"
    echo "versions as resolved on $(date -u +%Y-%m-%d) by \`scripts/measure_install.sh\`."
    echo
    echo "| Install | Size | Packages | Note |"
    echo "| --- | --- | --- | --- |"
} > "$work/table.md"
if [[ -n "${AGENTWIZ_SPEC:-}" ]]; then
    measure "AgentWiz requirements" "For comparison." "$AGENTWIZ_SPEC"
fi
measure "web-retrieval-mcp 0.1.1 \`requirements.txt\`" "The baseline." -r "$work/requirements-0.1.1.txt"
measure "**webSpell core** (\`httpx\`, \`trafilatura\`)" "The library form." "$wheel"
measure "\`rank-bm25\`" "Replaced by the built-in BM25." rank-bm25
measure "\`h2\`, \`hpack\`, \`hyperframe\`" "The \`httpx[http2]\` extra, dropped." h2
measure "\`pymupdf\` ${pymupdf_version:+(}${pymupdf_version}${pymupdf_version:+)}" "Removed." pymupdf
measure "\`pypdfium2\` ${pdfium_version}" "The \`pdf\` extra." pypdfium2
measure "\`fastmcp\` 4.1.6" "The \`server\` extra, as pinned in the image." fastmcp==4.1.6
measure "**webSpell core + pdf + server**" "The standalone server image's Python packages." \
    "${wheel}[server,pdf]" "fastmcp==4.1.6"

"$python" - "$work/table.md" docs/operations.md <<'PY'
import sys
from pathlib import Path
table = Path(sys.argv[1]).read_text()
doc = Path(sys.argv[2])
text = doc.read_text()
begin, end = "<!-- measure_install:begin -->", "<!-- measure_install:end -->"
head, _, rest = text.partition(begin)
_, _, tail = rest.partition(end)
doc.write_text(f"{head}{begin}\n{table}{end}{tail}")
PY
cat "$work/table.md"
