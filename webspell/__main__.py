"""``python -m webspell`` runs the MCP server (needs the ``server`` extra)."""
from __future__ import annotations

import sys


def _main() -> int:
    try:
        from .server import main
    except ModuleNotFoundError as exc:
        if exc.name and exc.name.split(".")[0] in ("fastmcp", "pydantic"):
            print("The webSpell MCP server needs the server extra: "
                  "pip install 'webSpell[server]'", file=sys.stderr)
            return 2
        raise
    main()
    return 0


if __name__ == "__main__":
    raise SystemExit(_main())
