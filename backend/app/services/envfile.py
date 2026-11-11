"""Safe updates to backend/.env (used by setup scripts and the admin email settings API)."""
from __future__ import annotations

import os
import re
import shutil
import tempfile
from pathlib import Path

ENV_PATH = Path(__file__).resolve().parents[2] / ".env"


def _quote(value: str) -> str:
    """Quote so python-dotenv reads the exact value back (passwords with $, #, spaces, quotes).
    '${' is rejected because python-dotenv expands ${VAR} in every value and has no escape."""
    if "${" in value:
        # python-dotenv expands ${VAR} even inside quotes and has no escape for it
        raise ValueError("values containing '${' cannot be stored in .env; choose a different password")
    if value == "" or re.fullmatch(r"[A-Za-z0-9_@.:/+%\-]*", value):
        return value
    if "'" not in value:
        return f"'{value}'"
    return '"' + value.replace("\\", "\\\\").replace('"', '\\"') + '"'


def update_env(values: dict[str, str], path: Path | None = None) -> None:
    """Set KEY=value lines (replace existing, else append). Keeps a .env.bak copy and writes
    atomically (temp file + replace) so a crash can never leave a half-written .env."""
    path = path or ENV_PATH
    content = path.read_text(encoding="utf-8-sig") if path.exists() else ""
    for key, value in values.items():
        if not re.fullmatch(r"[A-Z][A-Z0-9_]*", key):
            raise ValueError(f"invalid env key {key!r}")
        if "\n" in value or "\r" in value:
            raise ValueError(f"{key} must be a single line")
        line = f"{key}={_quote(value)}"
        if re.search(rf"(?m)^\s*{key}\s*=.*$", content):
            content = re.sub(rf"(?m)^\s*{key}\s*=.*$", lambda _: line, content, count=1)
        else:
            content = content.rstrip("\n") + "\n" + line + "\n"
    if path.exists():
        shutil.copyfile(path, path.with_suffix(".bak"))
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=".env.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(content)
        os.replace(tmp, path)
    except Exception:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise
