#!/usr/bin/env python3
"""Safely restore or migrate a recognized project without discarding registry history."""

from __future__ import annotations

import argparse
import csv
import io
import json
import shutil
import uuid
from datetime import datetime, timezone
from pathlib import Path

from inspect_project import inspect, is_linklike


PLUGIN_ROOT = Path(__file__).resolve().parents[3]
DISTRIBUTION_ROOT = PLUGIN_ROOT.parents[1].resolve()
STRUCTURE = json.loads((PLUGIN_ROOT / "schemas" / "project-structure.json").read_text(encoding="utf-8"))
SUPPORTED_MIGRATIONS = STRUCTURE.get("supported_migrations", {})


def version_tuple(value: object) -> tuple[int, ...] | None:
    try:
        return tuple(int(part) for part in str(value).split("."))
    except (TypeError, ValueError):
        return None


def validate_root(path: Path) -> Path:
    root = path.expanduser().resolve()
    if root == Path(root.anchor) or root == Path.home().resolve():
        raise ValueError("Refusing to repair an unsafe project directory")
    if root == DISTRIBUTION_ROOT or DISTRIBUTION_ROOT in root.parents:
        raise ValueError("Refusing to repair a user project inside the plugin distribution")
    control = root / ".home-control"
    if is_linklike(control):
        raise ValueError("Refusing to repair a linked .home-control directory")
    marker = control / "project.json"
    if is_linklike(marker):
        raise ValueError("Refusing to repair a linked project marker")
    project = json.loads(marker.read_text(encoding="utf-8"))
    if not isinstance(project, dict):
        raise ValueError("Project marker must be a JSON object")
    folder_binding = project.get("folder_binding", {})
    created_by = project.get("created_by", {})
    if not isinstance(folder_binding, dict) or not isinstance(created_by, dict):
        raise ValueError("Project marker folder_binding and created_by must be objects")
    bound = folder_binding.get("absolute_path") or project.get("project_root")
    missing_identity = [
        field
        for field in ("schema_version", "project_id", "name")
        if not isinstance(project.get(field), str) or not project[field].strip()
    ]
    if missing_identity:
        raise ValueError("Project marker is incomplete: " + ", ".join(missing_identity))
    if created_by.get("plugin_id") != STRUCTURE["plugin_id"]:
        raise ValueError("Project marker was not created by this plugin")
    raw_version = created_by.get("structure_version")
    current_version = version_tuple(raw_version)
    required_version = version_tuple(STRUCTURE["structure_version"])
    if current_version is None or required_version is None:
        raise ValueError("Project structure version is missing or invalid")
    if current_version > required_version:
        raise ValueError("Refusing to downgrade a project created by a newer plugin")
    if current_version < required_version:
        migration = SUPPORTED_MIGRATIONS.get(str(raw_version), {})
        if migration.get("to") != STRUCTURE["structure_version"] or migration.get("mode") != "additive":
            raise ValueError(
                f"No supported migration from {raw_version} to {STRUCTURE['structure_version']}"
            )
    elif raw_version != STRUCTURE["structure_version"]:
        raise ValueError("Project structure version has a non-canonical format")
    if not bound or Path(bound).expanduser().resolve() != root:
        raise ValueError("Project folder binding does not match the selected directory")
    recovery = control / "recovery"
    if is_linklike(recovery):
        raise ValueError("Refusing to use a linked recovery directory")
    if recovery.exists() and not recovery.is_dir():
        raise ValueError("Project recovery path exists but is not a directory")
    return root


def jsonl_error(path: Path) -> str | None:
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeError) as exc:
        return str(exc)
    for line_number, raw in enumerate(lines, 1):
        if not raw.strip():
            continue
        try:
            value = json.loads(raw)
        except json.JSONDecodeError as exc:
            return f"line {line_number}: {exc.msg}"
        if not isinstance(value, dict):
            return f"line {line_number}: expected a JSON object"
    return None


def documents_schema_migration_allowed(
    actual: object, expected: object, source_structure_version: object
) -> bool:
    migration = SUPPORTED_MIGRATIONS.get(str(source_structure_version), {})
    return (
        migration.get("to") == STRUCTURE["structure_version"]
        and migration.get("mode") == "additive"
        and migration.get("documents_schema_from") == actual
        and migration.get("documents_schema_to") == expected
    )


def collect_plan(root: Path) -> tuple[list[tuple[str, Path, object]], list[str]]:
    actions: list[tuple[str, Path, object]] = []
    conflicts: list[str] = []
    marker_action: tuple[str, Path, object] | None = None
    current_structure_version: object = None
    for relative in [*STRUCTURE["folders"], *STRUCTURE["control_directories"]]:
        target = root / relative
        if is_linklike(target) and (relative.startswith(".home-control") or not target.exists()):
            conflicts.append(f"{relative}: symbolic links and junctions are not allowed")
        elif not target.exists():
            actions.append(("create_directory", target, None))
        elif not target.is_dir():
            conflicts.append(f"{relative}: expected a directory")

    marker = root / ".home-control" / "project.json"
    if not marker.is_file():
        conflicts.append(".home-control/project.json: a valid project marker is required")
    else:
        project = json.loads(marker.read_text(encoding="utf-8"))
        current_version = project.get("created_by", {}).get("structure_version")
        current_structure_version = current_version
        if current_version != STRUCTURE["structure_version"]:
            marker_action = ("update_project_marker_version", marker, STRUCTURE["structure_version"])

    for relative, content in STRUCTURE["json_files"].items():
        target = root / relative
        if is_linklike(target):
            conflicts.append(f"{relative}: symbolic links and junctions are not allowed")
        elif not target.exists():
            actions.append(("create_json", target, content))
        elif not target.is_file():
            conflicts.append(f"{relative}: expected a file")
        else:
            try:
                parsed = json.loads(target.read_text(encoding="utf-8"))
            except (OSError, UnicodeError, json.JSONDecodeError) as exc:
                conflicts.append(f"{relative}: existing JSON cannot be replaced automatically: {exc}")
                continue
            if not isinstance(parsed, dict):
                conflicts.append(f"{relative}: expected a JSON object")
                continue
            if relative.endswith("documents.json"):
                if not isinstance(parsed.get("items"), list):
                    conflicts.append(f"{relative}: items must be an array")
                    continue
                actual_schema = parsed.get("schema_version")
                expected_schema = content.get("schema_version")
                if actual_schema != expected_schema:
                    if documents_schema_migration_allowed(
                        actual_schema, expected_schema, current_structure_version
                    ):
                        actions.append(("migrate_json_schema_version", target, expected_schema))
                    else:
                        conflicts.append(
                            f"{relative}: unsupported schema migration {actual_schema!r} -> {expected_schema!r}"
                        )

    for relative in STRUCTURE["jsonl_files"]:
        target = root / relative
        if is_linklike(target):
            conflicts.append(f"{relative}: symbolic links and junctions are not allowed")
        elif not target.exists():
            actions.append(("create_jsonl", target, None))
        elif not target.is_file():
            conflicts.append(f"{relative}: expected a file")
        else:
            error = jsonl_error(target)
            if error:
                conflicts.append(
                    f"{relative}: invalid append-only registry; automatic replacement is forbidden ({error})"
                )

    for relative, headers in STRUCTURE["csv_files"].items():
        target = root / relative
        if is_linklike(target):
            conflicts.append(f"{relative}: symbolic links and junctions are not allowed")
        elif not target.exists():
            actions.append(("create_csv", target, headers))
        elif not target.is_file():
            conflicts.append(f"{relative}: expected a file")
        else:
            try:
                with target.open("r", encoding="utf-8-sig", newline="") as handle:
                    reader = csv.DictReader(handle)
                    actual = list(reader.fieldnames or [])
                    rows = list(reader)
            except (OSError, UnicodeError, csv.Error) as exc:
                conflicts.append(f"{relative}: existing CSV cannot be migrated automatically: {exc}")
                continue
            if len(set(actual)) != len(actual) or any(
                None in row or any(value is None for value in row.values()) for row in rows
            ):
                conflicts.append(f"{relative}: ambiguous CSV columns or row widths require manual repair")
                continue
            if any(header not in actual for header in headers):
                actions.append(("migrate_csv_headers", target, headers))
    if marker_action:
        actions.append(marker_action)
    return actions, conflicts


def backup_file(root: Path, path: Path, backup_root: Path) -> Path:
    relative = path.relative_to(root)
    destination = backup_root / relative
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(path, destination)
    return destination


def atomic_write_text(target: Path, text: str, encoding: str = "utf-8") -> None:
    temporary = target.with_name(f".{target.name}.{uuid.uuid4().hex}.tmp")
    try:
        temporary.write_text(text, encoding=encoding)
        temporary.replace(target)
    finally:
        if temporary.exists():
            temporary.unlink()


def exclusive_write_text(target: Path, text: str, encoding: str = "utf-8") -> None:
    with target.open("x", encoding=encoding, newline="") as handle:
        handle.write(text)


def csv_text(headers: list[str], rows: list[dict[str, str]] | None = None) -> str:
    buffer = io.StringIO(newline="")
    writer = csv.DictWriter(buffer, fieldnames=headers, extrasaction="ignore")
    writer.writeheader()
    if rows:
        writer.writerows(rows)
    return buffer.getvalue()


def apply_action(root: Path, action: tuple[str, Path, object], backup_root: Path) -> str:
    kind, target, content = action
    target.parent.mkdir(parents=True, exist_ok=True)
    backup = None
    if kind in {"migrate_csv_headers", "migrate_json_schema_version"}:
        backup = backup_file(root, target, backup_root)
    elif kind == "update_project_marker_version":
        backup = backup_file(root, target, backup_root)
    if kind == "create_directory":
        target.mkdir(parents=True, exist_ok=False)
    elif kind == "create_json":
        exclusive_write_text(target, json.dumps(content, ensure_ascii=False, indent=2) + "\n")
    elif kind == "create_jsonl":
        exclusive_write_text(target, "")
    elif kind == "create_csv":
        exclusive_write_text(target, csv_text(content), encoding="utf-8-sig")
    elif kind == "migrate_json_schema_version":
        value = json.loads(target.read_text(encoding="utf-8"))
        value["schema_version"] = content
        atomic_write_text(target, json.dumps(value, ensure_ascii=False, indent=2) + "\n")
    elif kind == "migrate_csv_headers":
        with target.open("r", encoding="utf-8-sig", newline="") as handle:
            reader = csv.DictReader(handle)
            existing_headers = list(reader.fieldnames or [])
            rows = list(reader)
        output_headers = [*existing_headers, *(header for header in content if header not in existing_headers)]
        atomic_write_text(target, csv_text(output_headers, rows), encoding="utf-8-sig")
    elif kind == "update_project_marker_version":
        project = json.loads(target.read_text(encoding="utf-8"))
        project.setdefault("created_by", {})["structure_version"] = content
        atomic_write_text(target, json.dumps(project, ensure_ascii=False, indent=2) + "\n")
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
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S.%fZ") + "-" + uuid.uuid4().hex
    backup_root = root / ".home-control" / "recovery" / stamp
    if not args.apply:
        for action in actions:
            kind, target, _ = action
            suffix = " (existing file will be backed up first)" if kind in {"migrate_csv_headers", "migrate_json_schema_version", "update_project_marker_version"} else ""
            print(f"{kind}: {target}{suffix}")
        return 0

    non_marker_actions = [action for action in actions if action[0] != "update_project_marker_version"]
    for action in non_marker_actions:
        print(apply_action(root, action, backup_root))

    remaining, remaining_conflicts = collect_plan(root)
    remaining_non_marker = [action for action in remaining if action[0] != "update_project_marker_version"]
    if remaining_non_marker or remaining_conflicts:
        print("Repair did not reach a consistent pre-migration state; project marker was not advanced.")
        return 3

    marker_actions = [action for action in remaining if action[0] == "update_project_marker_version"]
    for action in marker_actions:
        print(apply_action(root, action, backup_root))

    result = inspect(root)
    if not result["gate_passed"]:
        marker = root / ".home-control" / "project.json"
        marker_backup = backup_root / marker.relative_to(root)
        if marker_backup.is_file():
            atomic_write_text(marker, marker_backup.read_text(encoding="utf-8"))
            print("Final inspection failed; the project marker was restored from backup.")
        else:
            print("Final inspection failed.")
        return 3
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
