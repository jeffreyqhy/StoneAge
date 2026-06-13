from __future__ import annotations

import os
import platform
import sys
from pathlib import Path


def restart_as_arm64_if_needed() -> None:
    if sys.platform != "darwin" or platform.machine() != "x86_64":
        return
    if os.environ.get("STONEAGE_ARM64_REEXEC") == "1":
        return
    os.environ["STONEAGE_ARM64_REEXEC"] = "1"
    os.execv("/usr/bin/arch", ["arch", "-arm64", sys.executable, "-m", "stoneage_studio", *sys.argv[1:]])


restart_as_arm64_if_needed()

from PySide6.QtWidgets import QApplication  # noqa: E402
from PySide6.QtGui import QIcon  # noqa: E402

from .main_window import MainWindow  # noqa: E402


def app_icon() -> QIcon:
    icon_path = Path(__file__).with_name("assets") / "app_icon.png"
    return QIcon(str(icon_path)) if icon_path.exists() else QIcon()


def main() -> int:
    app = QApplication(sys.argv)
    app.setApplicationName("StoneAge Script Studio")
    app.setOrganizationName("StoneAge")
    icon = app_icon()
    if not icon.isNull():
        app.setWindowIcon(icon)
    window = MainWindow()
    if not icon.isNull():
        window.setWindowIcon(icon)
    window.resize(1500, 920)
    window.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
