"""Разбор строк с лидами — общий для /import, /addlead, inbox и seed."""
from __future__ import annotations

import re

USERNAME_RE = re.compile(r"^@?([A-Za-z][A-Za-z0-9_]{3,31})$")


def parse_lead_line(line: str) -> tuple[str, str | None] | None:
    """'@channel ниша слов' | 'channel,ниша' (CSV) → (username, niche|None)."""
    line = line.strip()
    if not line:
        return None
    if "," in line and " " not in line.split(",", 1)[0]:
        username_part, niche_part = line.split(",", 1)
    else:
        parts = line.split(None, 1)
        username_part = parts[0]
        niche_part = parts[1] if len(parts) > 1 else ""
    m = USERNAME_RE.match(username_part.strip())
    if not m:
        return None
    niche = niche_part.strip() or None
    return m.group(1).lower(), niche
