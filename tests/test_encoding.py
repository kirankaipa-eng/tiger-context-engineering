"""Every text file we read or write says UTF-8. Without it Python uses the machine's code page, and on a
Windows client (cp1252) the first arrow or dash in a report fails the whole import."""

import ast
from pathlib import Path

SRC = Path(__file__).resolve().parents[1] / "src"


def test_text_files_are_read_and_written_as_utf8():
    missing = []
    for path in SRC.rglob("*.py"):
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)):
                continue
            name, keys = node.func.attr, {k.arg for k in node.keywords}
            binary = any(isinstance(a, ast.Constant) and "b" in str(a.value) for a in node.args)
            if (name in ("read_text", "write_text") or (name in ("open", "fdopen") and node.args and not binary
                                                         and isinstance(node.args[-1], ast.Constant))) \
                    and "encoding" not in keys:
                missing.append(f"{path.relative_to(SRC)}:{node.lineno} {name}")
    assert not missing, missing
