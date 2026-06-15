from __future__ import annotations

import subprocess
import sys


def subprocess_no_window_kwargs() -> dict[str, int]:
    if sys.platform.startswith("win"):
        return {"creationflags": subprocess.CREATE_NO_WINDOW}
    return {}
