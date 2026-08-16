#!/usr/bin/env python3
"""Create a non-destructive local home-project structure."""

from __future__ import annotations

import argparse
import csv
import json
import uuid
from datetime import datetime, timezone
from pathlib import Path

PLUGIN_ROOT = Path(__file__).resolve().parents[3]
STRUCTURE_FILE = PLUGIN_ROOT / "mcp" / "project-structure.json"
STRUCTURE = json.loads(STRUCTURE_FILE.read_text(encoding="utf-8"))
FOLDERS = STRUCTURE["folders"]
CSV_FILES = STRUCTURE["csv_files"]


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def validate_target(path: Path) -> Path:
    resolved = path.expanduser().resolve()
    if resolved == Path(resolved.anchor):
        raise ValueError("Refusing to use a filesystem root as a project directory")
    if resolved == Path.home().resolve():
        raise ValueError("Refusing to use the home directory as a project directory")
    return resolved


def create_text_if_missing(path: Path, text: str, dry_run: bool, created: list[str]) -> None:
    if path.exists():
        return
    created.append(str(path))
    if not dry_run:
        path.write_text(text, encoding="utf-8")


def create_csv_if_missing(path: Path, headers: list[str], dry_run: bool, created: list[str]) -> None:
    if path.exists():
        return
    created.append(str(path))
    if not dry_run:
        with path.open("w", encoding="utf-8-sig", newline="") as handle:
            csv.writer(handle).writerow(headers)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("project_dir", type=Path)
    parser.add_argument("--name", default=None, help="Project name; defaults to the folder name")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    root = validate_target(args.project_dir)
    created: list[str] = []

    for folder in FOLDERS:
        target = root / folder
        if not target.exists():
            created.append(str(target))
            if not args.dry_run:
                target.mkdir(parents=True, exist_ok=False)

    for relative in STRUCTURE["control_directories"]:
        target = root / relative
        if not target.exists():
            created.append(str(target))
            if not args.dry_run:
                target.mkdir(parents=True, exist_ok=False)

    control = root / ".home-control"

    created_at = utc_now()
    project = {
        "schema_version": "2.2",
        "project_id": str(uuid.uuid4()),
        "name": args.name or root.name,
        "created_by": {
            "plugin_id": STRUCTURE["plugin_id"],
            "structure_version": STRUCTURE["structure_version"],
        },
        "project_root": str(root),
        "folder_binding": {
            "absolute_path": str(root),
            "marker_relative_path": ".home-control/project.json",
            "bound_at_utc": created_at,
        },
        "storage_mode": "local",
        "status": "draft",
        "created_at_utc": created_at,
        "object_type": None,
        "project_stage": None,
        "location": None,
        "currency": None,
        "questionnaire": {"completed_blocks": [], "open_block": None, "updated_at_utc": None},
        "systems": {},
        "priorities": [],
        "constraints": [],
        "open_questions": [],
    }
    create_text_if_missing(control / "project.json", json.dumps(project, ensure_ascii=False, indent=2) + "\n", args.dry_run, created)
    for relative, content in STRUCTURE["json_files"].items():
        create_text_if_missing(root / relative, json.dumps(content, ensure_ascii=False, indent=2) + "\n", args.dry_run, created)
    for relative in STRUCTURE["jsonl_files"]:
        create_text_if_missing(root / relative, "", args.dry_run, created)
    for relative, headers in CSV_FILES.items():
        create_csv_if_missing(root / relative, headers, args.dry_run, created)

    mode = "Would create" if args.dry_run else "Created"
    print(f"{mode} {len(created)} item(s) in {root}")
    for item in created:
        print(item)
    if not created:
        print("Nothing changed; all managed items already exist.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
