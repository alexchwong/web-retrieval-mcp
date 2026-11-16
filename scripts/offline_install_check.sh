#!/usr/bin/env bash
# Offline install check (Section 15, test 11).
#
# Usage: bash scripts/offline_install_check.sh dist/webspell-X.Y.Z-py3-none-any.whl
#
# Prepares a wheel directory holding the wheel and every transitive requirement
# (this step needs the network), then installs core, [pdf], [server] and
# [server,pdf] from it with --no-index, as AgentWiz's offline smoke does.
set -euo pipefail

if [[ $# -ne 1 || ! -f "$1" ]]; then
    echo "Usage: bash scripts/offline_install_check.sh dist/webspell-X.Y.Z-py3-none-any.whl" >&2
    exit 2
fi
wheel="$(cd -- "$(dirname -- "$1")" && pwd)/$(basename -- "$1")"
python="${PYTHON:-python3}"
work="$(mktemp -d)"
trap 'rm -rf "$work"' EXIT

"$python" -m pip wheel --quiet --disable-pip-version-check -w "$work/wheels" "${wheel}[server,pdf]"

for extras in "" "[pdf]" "[server]" "[server,pdf]"; do
    "$python" -m venv "$work/venv"
    "$work/venv/bin/python" -m pip install --quiet --disable-pip-version-check \
        --no-index --find-links "$work/wheels" "webSpell${extras}"
    checks="import webspell; assert webspell.INTERFACE_VERSION == 1"
    [[ "$extras" == *pdf* ]] && checks+="; import pypdfium2"
    [[ "$extras" == *server* ]] && checks+="; import webspell.server"
    if [[ "$extras" != *pdf* ]]; then
        checks+="; from webspell import extract; assert not extract.pdf_available()"
    fi
    (cd "$work" && "$work/venv/bin/python" -I -c "$checks")
    echo "PASS offline install webSpell${extras:-} ($("$work/venv/bin/python" -m pip list --format=freeze | wc -l | tr -d ' ') packages)"
    rm -rf "$work/venv"
done
