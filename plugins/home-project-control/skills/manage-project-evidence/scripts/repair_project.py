#!/usr/bin/env python3
"""Safely restore a recognized project structure, backing up invalid service files."""

from __future__ import annotations

import argparse
import csv
import json
import shutil
from datetime import datetime, timezone
from pathlib import Path


PLUGIN_ROOT = Path(__file__).resolve().parents[3]
STRUCTURE = json.loads((PLUGIN_ROOT / "mcp" / "project-structure.json").read_text(encoding="utf-8"))


def validate_root(path: Path) -> Path:
    root = path.expanduser().resolve()
    if root == Path(root.anchor) or root == Path.home().resolve():
        raise ValueError("Refusing to repair an unsafe project directory")
    marker = root / ".home-control" / "project.json"
    project = json.loads(marker.read_text(encoding="utf-8"))
    bound = project.get("folder_binding", {}).get("absolute_path") or project.get("project_root")
    if not project.get("name") or not project.get("schema_version"):
        raise ValueError("Project marker is incomplete")
    if not bound or Path(bound).expanduser().resolve() != root:
        raise ValueError("Project folder binding does not match the selected directory")
    return root


def jsonl_valid(path: Path) -> bool:
    for raw in path.read_text(encoding="utf-8").splitlines():
        if not raw.strip():
            continue
        try:
            json.loads(raw)
        except json.JSONDecodeError:
            return False
    return True


def collect_plan(root: Path) -> tuple[list[tuple[str, Path, object]], list[str]]:
    actions: list[tuple[str, Path, object]] = []
    conflicts: list[str] = []
    for relative in [*STRUCTURE["folders"], *STRUCTURE["control_directories"]]:
        target = root / relative
        if not target.exists():
            actions.append(("create_directory", target, None))
        elif not target.is_dir():
            conflicts.append(f"{relative}: expected a directory")

    marker = root / ".home-control" / "project.json"
    if not marker.is_file():
        conflicts.append(".home-control/project.json: a valid project marker is required")

    for relative, content in STRUCTURE["json_files"].items():
        target = root / relative
        if not target.exists():
            actions.append(("create_json", target, content))
        elif not target.is_file():
            conflicts.append(f"{relative}: expected a file")
        else:
            try:
                parsed = json.loads(target.read_text(encoding="utf-8"))
                valid = not relative.endswith("documents.json") or isinstance(parsed.get("items"), list)
            except (json.JSONDecodeError, AttributeError):
                valid = False
            if not valid:
                actions.append(("replace_invalid_json", target, content))

    for relative in STRUCTURE["jsonl_files"]:
        target = root / relative
        if not target.exists():
            actions.append(("create_jsonl", target, None))
        elif not target.is_file():
            conflicts.append(f"{relative}: expected a file")
        elif not jsonl_valid(target):
            actions.append(("replace_invalid_jsonl", target, None))

    for relative, headers in STRUCTURE["csv_files"].items():
        target = root / relative
        if not target.exists():
            actions.append(("create_csv", target, headers))
        elif not target.is_file():
            conflicts.append(f"{relative}: expected a file")
        else:
            with target.open("r", encoding="utf-8-sig", newline="") as handle:
                actual = next(csv.reader(handle), [])
            if any(header not in actual for header in headers):
                actions.append(("replace_invalid_csv", target, headers))
    return actions, conflicts


def backup_file(root: Path, path: Path, backup_root: Path) -> Path:
    relative = path.relative_to(root)
    destination = backup_root / relative
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(path, destination)
    return destination


def apply_action(root: Path, action: tuple[str, Path, object], backup_root: Path) -> str:
    kind, target, content = action
    target.parent.mkdir(parents=True, exist_ok=True)
    backup = None
    if kind.startswith("replace_invalid_"):
        backup = backup_file(root, target, backup_root)
    if kind == "create_directory":
        target.mkdir(parents=True, exist_ok=False)
    elif kind in {"create_json", "replace_invalid_json"}:
        target.write_text(json.dumps(content, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    elif kind in {"create_jsonl", "replace_invalid_jsonl"}:
        target.write_text("", encoding="utf-8")
    elif kind in {"create_csv", "replace_invalid_csv"}:
        with target.open("w", encoding="utf-8-sig", newline="") as handle:
            csv.writer(handle).writerow(content)
    return f"{kind}: {target}" + (f" (backup: {backup})" if backup else "")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("project_dir", type=Path)
    parser.add_argument("--apply", action="store_true", help="Apply the displayed repair plan")
    args = parser.parse_args()
    root = validate_root(args.project_dir)
    actions, conflicts = collect_plan(root)
    if conflicts:
        print("Repair blocked because existing paths conflict with the required structure:")
        for item in conflicts:
            print(f"- {item}")
        return 2
    mode = "Repair plan" if not args.apply else "Applied repair"
    print(f"{mode}: {len(actions)} action(s) in {root}")
    if not actions:
        print("Nothing to repair.")
        return 0
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    backup_root = root / ".home-control" / "recovery" / stamp
    for action in actions:
        if args.apply:
            print(apply_action(root, action, backup_root))
        else:
            kind, target, _ = action
            suffix = " (existing file will be backed up first)" if kind.startswith("replace_invalid_") else ""
            print(f"{kind}: {target}{suffix}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
