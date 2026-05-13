#!/usr/bin/env python3
"""Summarize runner size snapshots into a Markdown report."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
from typing import Any


def fmt_gib(value: Any) -> str:
    if value is None:
        return "n/a"
    try:
        return f"{float(value):.1f} GiB"
    except (TypeError, ValueError):
        return "n/a"


def first_disk(snapshot: dict[str, Any], label: str) -> dict[str, Any] | None:
    for disk in snapshot.get("disks", []):
        if disk.get("label") == label:
            return disk
    return None


def workspace_disk(snapshot: dict[str, Any]) -> dict[str, Any] | None:
    return first_disk(snapshot, "workspace") or (snapshot.get("disks") or [None])[0]


def short(value: Any, limit: int = 64) -> str:
    text = str(value or "")
    if len(text) <= limit:
        return text
    return text[: limit - 1] + "..."


def load_snapshots(input_dir: Path) -> list[dict[str, Any]]:
    snapshots: list[dict[str, Any]] = []
    for path in sorted(input_dir.rglob("*.json")):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            print(f"Skipping invalid JSON {path}: {exc}")
            continue
        if isinstance(data, list):
            snapshots.extend(item for item in data if isinstance(item, dict))
        elif isinstance(data, dict):
            snapshots.append(data)
    return snapshots


def specs_table(snapshots: list[dict[str, Any]]) -> list[str]:
    lines = [
        "## Runner Specs",
        "",
        "| Label | Runner | Phase | OS | Arch | CPU | RAM | Workspace Total | Workspace Free | Image |",
        "|---|---|---|---|---|---:|---:|---:|---:|---|",
    ]
    for snap in snapshots:
        disk = workspace_disk(snap) or {}
        os_info = snap.get("os", {})
        env = snap.get("env", {})
        cpu = snap.get("cpu", {})
        image = env.get("ImageOS") or env.get("ImageVersion") or ""
        lines.append(
            "| {label} | {runner} | {phase} | {os_name} | {arch} | {cpu_count} | {ram} | {total} | {free} | {image} |".format(
                label=snap.get("label", ""),
                runner=snap.get("runner", ""),
                phase=snap.get("phase", ""),
                os_name=short(os_info.get("platform"), 42),
                arch=os_info.get("machine", ""),
                cpu_count=cpu.get("logical_count") or "n/a",
                ram=fmt_gib(snap.get("memory", {}).get("total_gib")),
                total=fmt_gib(disk.get("total_gib")),
                free=fmt_gib(disk.get("free_gib")),
                image=short(image, 32),
            )
        )
    return lines


def cleanup_delta_table(snapshots: list[dict[str, Any]]) -> list[str]:
    by_label: dict[str, list[dict[str, Any]]] = {}
    for snap in snapshots:
        by_label.setdefault(str(snap.get("label", "")), []).append(snap)

    lines = [
        "## Cleanup Delta",
        "",
        "| Label | Before Free | After Free | Delta |",
        "|---|---:|---:|---:|",
    ]
    found = False
    for label, group in sorted(by_label.items()):
        before = next((snap for snap in group if snap.get("phase") == "before"), None)
        after = next((snap for snap in group if snap.get("phase") != "before"), None)
        if not before or not after:
            continue
        before_disk = workspace_disk(before) or {}
        after_disk = workspace_disk(after) or {}
        before_free = before_disk.get("free_gib")
        after_free = after_disk.get("free_gib")
        delta = None
        if before_free is not None and after_free is not None:
            delta = round(float(after_free) - float(before_free), 1)
        lines.append(f"| {label} | {fmt_gib(before_free)} | {fmt_gib(after_free)} | {fmt_gib(delta)} |")
        found = True
    if not found:
        lines.append("| n/a | n/a | n/a | n/a |")
    return lines


def cleanup_candidates_table(snapshots: list[dict[str, Any]]) -> list[str]:
    lines = [
        "## Cleanup Candidates",
        "",
        "| Label | Phase | Path | Exists | Size | Notes |",
        "|---|---|---|---:|---:|---|",
    ]
    rows: list[tuple[float, str]] = []
    for snap in snapshots:
        for item in snap.get("cleanup_candidates", []):
            if not item.get("exists"):
                continue
            size = item.get("size_gib")
            sort_size = float(size or 0)
            note = "partial scan" if item.get("truncated") else ""
            row = (
                f"| {snap.get('label', '')} | {snap.get('phase', '')} | `{item.get('path', '')}` | "
                f"{item.get('exists')} | {fmt_gib(size)} | {note} |"
            )
            rows.append((sort_size, row))
    for _, row in sorted(rows, reverse=True)[:40]:
        lines.append(row)
    if not rows:
        lines.append("| n/a | n/a | n/a | n/a | n/a | n/a |")
    return lines


def raw_artifacts_note() -> list[str]:
    return [
        "## Notes",
        "",
        "- `Workspace Free` is measured on the filesystem that contains `GITHUB_WORKSPACE`.",
        "- Cleanup candidate sizes are best-effort. Windows scans are time-limited and may be partial.",
        "- The Linux cleanup row uses `jlumbroso/free-disk-space@main` with Android, .NET, Haskell, large packages, Docker images, and swap cleanup enabled; tool-cache cleanup is left off.",
        "- Raw JSON snapshots are included in the `runner-sizechart-report` artifact for deeper inspection.",
    ]


def build_report(snapshots: list[dict[str, Any]]) -> str:
    snapshots = sorted(snapshots, key=lambda item: (str(item.get("label", "")), str(item.get("phase", ""))))
    lines = [
        "# Runner Size Chart Report",
        "",
        f"Snapshots: {len(snapshots)}",
        "",
    ]
    for section in (specs_table, cleanup_delta_table, cleanup_candidates_table):
        lines.extend(section(snapshots))
        lines.append("")
    lines.extend(raw_artifacts_note())
    lines.append("")
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", required=True, help="Directory containing snapshot JSON files")
    parser.add_argument("--output", required=True, help="Markdown report path")
    parser.add_argument("--json-output", required=True, help="Combined JSON output path")
    args = parser.parse_args()

    input_dir = Path(args.input)
    snapshots = load_snapshots(input_dir)

    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    report = build_report(snapshots)
    output.write_text(report, encoding="utf-8")

    json_output = Path(args.json_output)
    json_output.parent.mkdir(parents=True, exist_ok=True)
    json_output.write_text(json.dumps(snapshots, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    summary_path = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary_path:
        with Path(summary_path).open("a", encoding="utf-8") as handle:
            handle.write(report)

    print(f"Wrote {output}")
    print(f"Wrote {json_output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
