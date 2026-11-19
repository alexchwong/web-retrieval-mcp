"""``import webspell`` has no side effects (Section 15, test 2)."""
from __future__ import annotations

import json
import os
import subprocess
import sys
import textwrap
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
# Standard-library modules webspell imports; their own import-time behaviour
# (for example asyncio importing subprocess) is the baseline, not webspell's.
STDLIB = ("asyncio,calendar,copy,dataclasses,datetime,importlib,ipaddress,json,math,re,"
          "random,socket,ssl,threading,typing,urllib.parse")

PROBE = textwrap.dedent(r'''
    import json, logging, os, sys

    events = []

    class Environ(type(os.environ)):
        pass

    real = os.environ

    class Watch(dict):
        def __getitem__(self, key):
            events.append(["env", key]); return real[key]
        def get(self, key, default=None):
            events.append(["env", key]); return real.get(key, default)
        def __contains__(self, key):
            events.append(["env", key]); return key in real
        def __iter__(self):
            events.append(["env", "*"]); return iter(real)
        def keys(self):
            events.append(["env", "*"]); return real.keys()
        def items(self):
            events.append(["env", "*"]); return real.items()

    def hook(event, args):
        if event == "open":
            path = args[0]
            if isinstance(path, str) and not path.endswith((".py", ".pyc", ".so", ".pth")) \
                    and "__pycache__" not in path and not os.path.isdir(path):
                events.append(["open", path])
        elif event in ("os.putenv", "os.unsetenv"):
            events.append(["setenv", str(args[0])])

    before_handlers = list(logging.getLogger().handlers)
    targets = sys.argv[1].split(",")
    os.environ = Watch()
    sys.addaudithook(hook)
    for target in targets:
        __import__(target)
    os.environ = real
    configured = (logging.getLogger().handlers != before_handlers
                  or logging.getLogger("webspell").handlers != [])
    print(json.dumps({"events": events, "modules": sorted(sys.modules), "logging": configured}))
''')


def probe(module: str) -> dict:
    output = subprocess.run([sys.executable, "-I", "-c", PROBE, module], capture_output=True,
                            text=True, check=True, cwd=ROOT, env={**os.environ, "PYTHONPATH": str(ROOT)})
    return json.loads(output.stdout.strip().splitlines()[-1])


class ImportTests(unittest.TestCase):
    def test_import_has_no_side_effects(self):
        # Interpreter start-up noise (site, sitecustomize) is excluded by comparing
        # with a baseline that imports the standard modules webspell itself uses.
        baseline = probe(STDLIB)
        result = probe("webspell")
        noise = {tuple(event) for event in baseline["events"]}
        events = [event for event in result["events"] if tuple(event) not in noise]
        self.assertEqual([e for e in events if e[0] in ("env", "setenv")], [])
        self.assertEqual([e for e in events if e[0] == "open"], [])
        for module in ("fastmcp", "pypdfium2", "numpy", "rank_bm25", "trafilatura", "webspell.server"):
            with self.subTest(module=module):
                self.assertNotIn(module, result["modules"])
        self.assertFalse(result["logging"])


if __name__ == "__main__":
    unittest.main()
