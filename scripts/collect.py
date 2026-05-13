#!/usr/bin/env python3
"""Collect a small, portable GitHub runner size snapshot."""

from __future__ import annotations

import argparse
import ctypes
import datetime as dt
import json
import os
import platform
import shutil
import subprocess
import sys
import time
from pathlib import Path
from typing import Any


GIB = 1024**3


def now_utc() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")


def gib(value: int | None) -> float | None:
    if value is None:
        return None
    return round(value / GIB, 3)


def run(command: list[str], timeout: int = 20) -> str | None:
    try:
        completed = subprocess.run(
            command,
            check=False,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
        )
    except (FileNotFoundError, subprocess.SubprocessError):
        return None
    output = (completed.stdout or "").strip()
    return output or None


def read_first_line(path: Path) -> str | None:
    try:
        with path.open("r", encoding="utf-8", errors="replace") as handle:
            line = handle.readline().strip()
        return line or None
    except OSError:
        return None


def linux_mem_total() -> int | None:
    try:
        with Path("/proc/meminfo").open("r", encoding="utf-8") as handle:
            for line in handle:
                if line.startswith("MemTotal:"):
                    parts = line.split()
                    return int(parts[1]) * 1024
    except (OSError, ValueError, IndexError):
        return None
    return None


def mac_mem_total() -> int | None:
    output = run(["sysctl", "-n", "hw.memsize"])
    if not output:
        return None
    try:
        return int(output.splitlines()[0])
    except ValueError:
        return None


def windows_mem_total() -> int | None:
    class MemoryStatusEx(ctypes.Structure):
        _fields_ = [
            ("dwLength", ctypes.c_ulong),
            ("dwMemoryLoad", ctypes.c_ulong),
            ("ullTotalPhys", ctypes.c_ulonglong),
            ("ullAvailPhys", ctypes.c_ulonglong),
            ("ullTotalPageFile", ctypes.c_ulonglong),
            ("ullAvailPageFile", ctypes.c_ulonglong),
            ("ullTotalVirtual", ctypes.c_ulonglong),
            ("ullAvailVirtual", ctypes.c_ulonglong),
            ("ullAvailExtendedVirtual", ctypes.c_ulonglong),
        ]

    status = MemoryStatusEx()
    status.dwLength = ctypes.sizeof(MemoryStatusEx)
    if ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(status)):
        return int(status.ullTotalPhys)
    return None


def memory_total() -> int | None:
    system = platform.system()
    if system == "Linux":
        return linux_mem_total()
    if system == "Darwin":
        return mac_mem_total()
    if system == "Windows":
        return windows_mem_total()
    return None


def cpu_model() -> str | None:
    system = platform.system()
    if system == "Linux":
        line = run(["sh", "-c", "grep -m1 'model name' /proc/cpuinfo | cut -d: -f2-"])
        return line.strip() if line else platform.processor() or None
    if system == "Darwin":
        return run(["sysctl", "-n", "machdep.cpu.brand_string"]) or platform.processor() or None
    if system == "Windows":
        output = run(
            [
                "powershell",
                "-NoProfile",
                "-Command",
                "(Get-CimInstance Win32_Processor | Select-Object -First 1 -ExpandProperty Name)",
            ]
        )
        return output or os.environ.get("PROCESSOR_IDENTIFIER") or platform.processor() or None
    return platform.processor() or None


def disk_entry(label: str, path: Path) -> dict[str, Any] | None:
    try:
        usage = shutil.disk_usage(path)
    except OSError:
        return None
    return {
        "label": label,
        "path": str(path),
        "total_bytes": usage.total,
        "used_bytes": usage.used,
        "free_bytes": usage.free,
        "total_gib": gib(usage.total),
        "used_gib": gib(usage.used),
        "free_gib": gib(usage.free),
    }


def existing_path(value: str | None) -> Path | None:
    if not value:
        return None
    path = Path(value)
    if path.exists():
        return path
    return None


def disk_paths() -> list[tuple[str, Path]]:
    items: list[tuple[str, Path]] = []
    env_paths = [
        ("workspace", os.environ.get("GITHUB_WORKSPACE")),
        ("runner_temp", os.environ.get("RUNNER_TEMP")),
        ("runner_tool_cache", os.environ.get("RUNNER_TOOL_CACHE") or os.environ.get("AGENT_TOOLSDIRECTORY")),
        ("home", str(Path.home())),
    ]
    for label, value in env_paths:
        path = existing_path(value)
        if path:
            items.append((label, path))

    if platform.system() == "Windows":
        for letter in "CDEFGHIJKLMNOPQRSTUVWXYZ":
            root = Path(f"{letter}:\\")
            if root.exists():
                items.append((f"drive_{letter}", root))
    else:
        items.append(("root", Path("/")))
        for mount in (Path("/mnt"), Path("/Volumes")):
            if mount.exists():
                items.append((str(mount), mount))

    seen: set[str] = set()
    unique: list[tuple[str, Path]] = []
    for label, path in items:
        key = str(path.resolve()) if path.exists() else str(path)
        if key in seen:
            continue
        seen.add(key)
        unique.append((label, path))
    return unique


def du_size(path: Path, timeout: int = 45) -> tuple[int | None, bool]:
    if platform.system() != "Windows":
        output = run(["du", "-sk", str(path)], timeout=timeout)
        if not output:
            return None, False
        try:
            return int(output.split()[0]) * 1024, False
        except (ValueError, IndexError):
            return None, False

    start = time.monotonic()
    total = 0
    truncated = False
    stack = [path]
    while stack:
        if time.monotonic() - start > timeout:
            truncated = True
            break
        current = stack.pop()
        try:
            with os.scandir(current) as iterator:
                for entry in iterator:
                    try:
                        if entry.is_file(follow_symlinks=False):
                            total += entry.stat(follow_symlinks=False).st_size
                        elif entry.is_dir(follow_symlinks=False):
                            stack.append(Path(entry.path))
                    except OSError:
                        continue
        except OSError:
            continue
    return total, truncated


def cleanup_candidate_paths() -> list[str]:
    system = platform.system()
    if system == "Linux":
        return [
            "/usr/local/lib/android",
            "/usr/share/dotnet",
            "/opt/ghc",
            "/usr/local/.ghcup",
            "/opt/hostedtoolcache",
            "/usr/local/share/boost",
            "/usr/local/share/chromium",
            "/usr/local/share/powershell",
            "/opt/az",
        ]
    if system == "Darwin":
        return [
            "/Applications/Xcode.app",
            "/Library/Developer/CoreSimulator",
            "/Users/runner/hostedtoolcache",
            "/usr/local/share/dotnet",
            "/usr/local/lib/android",
            "/opt/homebrew",
            "/usr/local/Homebrew",
        ]
    if system == "Windows":
        return [
            r"C:\hostedtoolcache",
            r"C:\Program Files\dotnet",
            r"C:\Android",
            r"C:\ProgramData\chocolatey\lib",
            r"C:\Program Files\Microsoft Visual Studio\2022",
            r"C:\Program Files (x86)\Windows Kits",
        ]
    return []


def cleanup_candidates() -> list[dict[str, Any]]:
    candidates: list[dict[str, Any]] = []
    timeout = 12 if platform.system() == "Windows" else 45
    for raw in cleanup_candidate_paths():
        path = Path(raw)
        exists = path.exists()
        size_bytes = None
        truncated = False
        if exists:
            size_bytes, truncated = du_size(path, timeout=timeout)
        candidates.append(
            {
                "path": raw,
                "exists": exists,
                "size_bytes": size_bytes,
                "size_gib": gib(size_bytes),
                "truncated": truncated,
            }
        )
    return candidates


def raw_commands() -> dict[str, str | None]:
    system = platform.system()
    values: dict[str, str | None] = {}
    if system == "Linux":
        values["lscpu"] = run(["lscpu"])
        values["free_h"] = run(["free", "-h"])
        values["df_h"] = run(["df", "-h"])
    elif system == "Darwin":
        values["sysctl_hw"] = run(["sysctl", "hw.ncpu", "hw.memsize", "machdep.cpu.brand_string"])
        values["df_h"] = run(["df", "-h"])
    elif system == "Windows":
        values["computer_info"] = run(
            [
                "powershell",
                "-NoProfile",
                "-Command",
                "Get-ComputerInfo -Property OsName,OsVersion,CsProcessors,CsTotalPhysicalMemory | Format-List",
            ],
            timeout=30,
        )
        values["ps_drives"] = run(
            [
                "powershell",
                "-NoProfile",
                "-Command",
                "Get-PSDrive -PSProvider FileSystem | Format-Table -AutoSize",
            ]
        )
    return values


def snapshot(args: argparse.Namespace) -> dict[str, Any]:
    total_memory = memory_total()
    disks = []
    for label, path in disk_paths():
        entry = disk_entry(label, path)
        if entry:
            disks.append(entry)

    interesting_env = {
        key: os.environ.get(key)
        for key in [
            "ImageOS",
            "ImageVersion",
            "RUNNER_OS",
            "RUNNER_ARCH",
            "RUNNER_NAME",
            "RUNNER_TEMP",
            "RUNNER_TOOL_CACHE",
            "AGENT_TOOLSDIRECTORY",
            "GITHUB_WORKSPACE",
        ]
        if os.environ.get(key)
    }

    return {
        "schema_version": 1,
        "collected_at_utc": now_utc(),
        "label": args.label,
        "runner": args.runner,
        "phase": args.phase,
        "note": args.note,
        "os": {
            "system": platform.system(),
            "release": platform.release(),
            "version": platform.version(),
            "machine": platform.machine(),
            "platform": platform.platform(),
            "python": sys.version.split()[0],
        },
        "env": interesting_env,
        "cpu": {
            "logical_count": os.cpu_count(),
            "model": cpu_model(),
        },
        "memory": {
            "total_bytes": total_memory,
            "total_gib": gib(total_memory),
        },
        "disks": disks,
        "cleanup_candidates": cleanup_candidates(),
        "raw": raw_commands(),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--label", required=True)
    parser.add_argument("--runner", required=True)
    parser.add_argument("--phase", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--note", default="")
    args = parser.parse_args()

    data = snapshot(args)
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(f"Wrote {output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
