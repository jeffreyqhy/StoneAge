from __future__ import annotations

import os
import shutil
import socket
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class DeviceInfo:
    serial: str
    status: str


class AdbError(RuntimeError):
    pass


class AdbClient:
    def __init__(self, executable: str = "adb", serial: str | None = None) -> None:
        self.executable = resolve_adb_executable(executable)
        self.serial = serial

    def _base(self) -> list[str]:
        cmd = [self.executable]
        if self.serial:
            cmd.extend(["-s", self.serial])
        return cmd

    def devices(self) -> list[DeviceInfo]:
        try:
            result = subprocess.run(
                [self.executable, "devices"],
                check=True,
                capture_output=True,
                text=True,
                timeout=8,
            )
        except subprocess.CalledProcessError as exc:
            detail = (exc.stderr or exc.stdout or str(exc)).strip()
            raise AdbError(f"ADB 不可用：{detail}") from exc
        except (OSError, subprocess.SubprocessError) as exc:
            raise AdbError(f"ADB 不可用：{exc}") from exc

        devices: list[DeviceInfo] = []
        for line in result.stdout.splitlines()[1:]:
            line = line.strip()
            if not line:
                continue
            parts = line.split()
            if len(parts) >= 2:
                devices.append(DeviceInfo(parts[0], parts[1]))
        return devices

    def connect(self, address: str) -> str:
        try:
            result = subprocess.run(
                [self.executable, "connect", address],
                check=True,
                capture_output=True,
                text=True,
                timeout=8,
            )
        except subprocess.CalledProcessError as exc:
            detail = (exc.stderr or exc.stdout or str(exc)).strip()
            raise AdbError(f"ADB 连接 {address} 失败：{detail}") from exc
        except (OSError, subprocess.SubprocessError) as exc:
            raise AdbError(f"ADB 连接 {address} 失败：{exc}") from exc
        output = (result.stdout or result.stderr).strip()
        lowered = output.lower()
        failed_markers = ("failed", "cannot", "unable", "refused", "no route", "timed out")
        ok_markers = ("connected to", "already connected to")
        if any(marker in lowered for marker in failed_markers) and not any(marker in lowered for marker in ok_markers):
            raise AdbError(f"ADB 连接 {address} 失败：{output or '未知错误'}")
        self.serial = address
        return output

    def discover_mumu_endpoints(self, preferred: str | None = None) -> list[str]:
        endpoints: set[str] = set()
        preferred_port = _port_from_endpoint(preferred)
        for device in self.devices():
            if device.status == "device" and _is_local_adb_serial(device.serial):
                endpoints.add(_normalize_local_endpoint(device.serial))

        ports = _candidate_mumu_ports(preferred_port)
        for port in ports:
            endpoint = f"127.0.0.1:{port}"
            if endpoint in endpoints:
                continue
            if _tcp_port_open("127.0.0.1", port):
                endpoints.add(endpoint)
        return sorted(endpoints, key=lambda endpoint: (_port_from_endpoint(endpoint) or 0, endpoint))

    def screencap_png(self) -> bytes:
        cmd = self._base() + ["exec-out", "screencap", "-p"]
        try:
            data = subprocess.check_output(cmd, timeout=8)
        except (OSError, subprocess.SubprocessError) as exc:
            raise AdbError(f"截图失败：{exc}") from exc
        if not data:
            raise AdbError("截图失败：ADB 返回空数据")
        return data

    def tap(self, x: int, y: int) -> None:
        cmd = self._base() + ["shell", "input", "tap", str(int(x)), str(int(y))]
        self._run_action(cmd, "点击")

    def long_press(self, x: int, y: int, duration_ms: int = 800) -> None:
        cmd = self._base() + [
            "shell",
            "input",
            "swipe",
            str(int(x)),
            str(int(y)),
            str(int(x)),
            str(int(y)),
            str(int(duration_ms)),
        ]
        self._run_action(cmd, "长按")

    def swipe(
        self,
        x1: int,
        y1: int,
        x2: int,
        y2: int,
        duration_ms: int = 400,
    ) -> None:
        cmd = self._base() + [
            "shell",
            "input",
            "swipe",
            str(int(x1)),
            str(int(y1)),
            str(int(x2)),
            str(int(y2)),
            str(int(duration_ms)),
        ]
        self._run_action(cmd, "滑动")

    def text(self, value: str) -> None:
        escaped = value.replace(" ", "%s")
        cmd = self._base() + ["shell", "input", "text", escaped]
        self._run_action(cmd, "输入")

    def _run_action(self, cmd: list[str], label: str) -> None:
        try:
            subprocess.run(cmd, check=True, capture_output=True, timeout=8)
        except (OSError, subprocess.SubprocessError) as exc:
            raise AdbError(f"ADB {label}失败：{exc}") from exc


def resolve_adb_executable(executable: str = "adb") -> str:
    if executable != "adb":
        return executable

    adb_name = "adb.exe" if sys.platform.startswith("win") else "adb"
    candidates: list[str] = []

    env_adb = os.environ.get("STONEAGE_ADB")
    if env_adb:
        candidates.append(env_adb)

    env_adb_dir = os.environ.get("STONEAGE_ADB_DIR")
    if env_adb_dir:
        candidates.append(str(Path(env_adb_dir) / adb_name))

    exe_dir = Path(sys.executable).resolve().parent
    candidates.extend(
        [
            str(exe_dir / "support" / "platform-tools" / adb_name),
            str(exe_dir / "platform-tools" / adb_name),
            str(Path.cwd() / "support" / "platform-tools" / adb_name),
        ]
    )

    for sdk_env in ("ANDROID_HOME", "ANDROID_SDK_ROOT"):
        sdk_root = os.environ.get(sdk_env)
        if sdk_root:
            candidates.append(str(Path(sdk_root) / "platform-tools" / adb_name))

    for candidate in candidates:
        if Path(candidate).exists():
            return candidate

    found = shutil.which("adb")
    if found:
        return found
    for candidate in (
        "/opt/homebrew/bin/adb",
        "/usr/local/bin/adb",
        str(Path.home() / "Library/Android/sdk/platform-tools/adb"),
        str(Path.home() / "Android/Sdk/platform-tools/adb"),
    ):
        if Path(candidate).exists():
            return candidate
    return executable


def _port_from_endpoint(endpoint: str | None) -> int | None:
    if not endpoint:
        return None
    value = endpoint.strip()
    if ":" in value:
        tail = value.rsplit(":", 1)[1]
    else:
        tail = value.rsplit(".", 1)[-1]
    return int(tail) if tail.isdigit() else None


def _is_local_adb_serial(serial: str) -> bool:
    value = serial.strip()
    if value.startswith("127.0.0.1:") or value.startswith("localhost:"):
        return True
    if value.startswith("127.0.0.") and ":" not in value:
        tail = value.rsplit(".", 1)[-1]
        return tail.isdigit()
    return False


def _normalize_local_endpoint(serial: str) -> str:
    value = serial.strip()
    if value.startswith("localhost:"):
        return f"127.0.0.1:{value.rsplit(':', 1)[1]}"
    if value.startswith("127.0.0.") and ":" not in value:
        prefix, port = value.rsplit(".", 1)
        if port.isdigit():
            return f"{prefix}:{port}"
    return value


def _candidate_mumu_ports(preferred_port: int | None = None) -> list[int]:
    ports: set[int] = set()
    if preferred_port:
        ports.add(preferred_port)
    ports.update({5555, 5556, 7555, 7556, 7557, 7558, 16384})
    ports.update(range(16384, 16896))
    return sorted(port for port in ports if 0 < port < 65536)


def _tcp_port_open(host: str, port: int, timeout: float = 0.035) -> bool:
    try:
        with socket.create_connection((host, int(port)), timeout=timeout):
            return True
    except OSError:
        return False
