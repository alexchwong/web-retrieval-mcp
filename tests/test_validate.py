"""The validator enforces exactly the advertised ``input_schema`` (Section 15, test 10)."""
from __future__ import annotations

import importlib.util
import itertools
import unittest

import webspell
from webspell import validate

HAVE_JSONSCHEMA = importlib.util.find_spec("jsonschema") is not None

SAMPLES = {
    "string": ["", "x", "general", "news", "science", "it", "images", "q" * 1000, "q" * 1001, "u" * 4096, "u" * 4097, "ü" * 1000],
    "other": [None, 0, 1, -1, 7, True, False, 1.0, 2.5, [], ["a"], ["a"] * 5, ["a"] * 6,
              ["a", 1], ["u" * 4097], {}, {"a": 1}],
}
VALUES = SAMPLES["string"] + SAMPLES["other"]


def schema_validator(schema):
    import jsonschema

    # JSON Schema's "integer" accepts 1.0 and Python's int accepts True; webSpell
    # accepts neither, so "integer" here means a Python int that is not a bool.
    checker = jsonschema.Draft202012Validator.TYPE_CHECKER.redefine(
        "integer", lambda _checker, value: isinstance(value, int) and not isinstance(value, bool))
    cls = jsonschema.validators.extend(jsonschema.Draft202012Validator, type_checker=checker)
    cls.check_schema(schema)
    return cls(schema)


def cases(tool):
    properties = list(next(t for t in webspell.TOOLS if t["name"] == tool)["input_schema"]["properties"])
    required = next(t for t in webspell.TOOLS if t["name"] == tool)["input_schema"]["required"]
    base = {name: "x" if name in ("query", "url") else None for name in required}
    yield {}
    yield {"unknown": 1, **base}
    yield base
    for name in properties:
        for value in VALUES:
            yield {**base, name: value}
    for first, second in itertools.combinations(properties, 2):
        for a, b in itertools.product(VALUES[::3], VALUES[1::4]):
            yield {**base, first: a, second: b}


@unittest.skipUnless(HAVE_JSONSCHEMA, "needs jsonschema from the dev extra")
class ValidatorSchemaTests(unittest.TestCase):
    def test_validator_agrees_with_each_schema(self):
        for tool in webspell.TOOLS:
            checker = schema_validator(tool["input_schema"])
            count = 0
            for arguments in cases(tool["name"]):
                count += 1
                schema_ok = checker.is_valid(arguments)
                try:
                    validate.check(tool["name"], arguments)
                    ours = True
                except validate.InvalidInput:
                    ours = False
                if schema_ok != ours:
                    self.fail(f"{tool['name']}: schema={schema_ok} validator={ours} for {arguments!r}"[:400])
            self.assertGreater(count, 150)

    def test_tools_match_validator_fields(self):
        for tool in webspell.TOOLS:
            schema = tool["input_schema"]
            self.assertEqual(set(schema["properties"]), set(validate.FIELDS[tool["name"]]))
            self.assertEqual(set(schema["required"]),
                             {k for k, (_, required, _) in validate.FIELDS[tool["name"]].items() if required})
            self.assertFalse(schema["additionalProperties"])
            for name, (_, _, default) in validate.FIELDS[tool["name"]].items():
                if name not in schema["required"]:
                    self.assertEqual(schema["properties"][name].get("default"), default)


class ValidatorTests(unittest.TestCase):
    def test_no_coercion_and_messages(self):
        check = validate.check
        for arguments, message in [
            ({"query": 1}, "query must be a string"),
            ({"query": "x", "max_passages": "3"}, "max_passages must be an integer"),
            ({"query": "x", "max_passages": True}, "max_passages must be an integer"),
            ({"query": "x", "max_passages": 3.0}, "max_passages must be an integer"),
            ({"query": "x", "extra": 1}, "unknown argument: extra"),
            ({}, "query is required"),
            ({"query": "q" * 1001}, "query must be at most 1000 characters"),
            ({"query": "x", "urls": ["u" * 4097]}, "URL item 1 must be at most 4096 characters"),
            ({"query": "x", "domains": []}, "domains must contain one to five DNS names"),
        ]:
            with self.subTest(arguments=str(arguments)[:60]), self.assertRaisesRegex(
                    validate.InvalidInput, message):
                check("web_evidence_passages", arguments)
        with self.assertRaisesRegex(validate.InvalidInput, "url must be at most 4096"):
            check("web_page_text", {"url": "u" * 4097})
        with self.assertRaisesRegex(validate.InvalidInput, "start must be at least 0"):
            check("web_page_text", {"url": "x", "start": -1})
        with self.assertRaisesRegex(validate.InvalidInput, "arguments must be an object"):
            check("web_page_text", ["x"])
        self.assertEqual(check("web_page_text", {"url": "x"}), {"url": "x", "start": 0, "max_chars": None})


if __name__ == "__main__":
    unittest.main()
