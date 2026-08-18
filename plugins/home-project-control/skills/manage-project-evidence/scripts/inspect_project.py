#!/usr/bin/env python3
"""Inspect a local project workspace without changing any files."""

from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path


PLUGIN_ROOT = Path(__file__).resolve().parents[3]
DISTRIBUTION_ROOT = PLUGIN_ROOT.parents[1].resolve()
STRUCTURE = json.loads((PLUGIN_ROOT / "schemas" / "project-structure.json").read_text(encoding="utf-8"))
SUPPORTED_MIGRATIONS = STRUCTURE.get("supported_migrations", {})


def version_tuple(value: object) -> tuple[int, ...] | None:
    try:
        return tuple(int(part) for part in str(value).split("."))
    except (TypeError, ValueError):
        return None


def is_linklike(path: Path) -> bool:
    if path.is_symlink():
        return True
    is_junction = getattr(path, "is_junction", None)
    return bool(is_junction and is_junction())


def read_json(path: Path) -> object:
    return json.loads(path.read_text(encoding="utf-8"))


def inspect_jsonl(path: Path) -> list[str]:
    errors: list[str] = []
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeError) as exc:
        return [f"{path.name}: {exc}"]
    for line_number, raw in enumerate(lines, 1):
        if not raw.strip():
            continue
        try:
            value = json.loads(raw)
        except json.JSONDecodeError as exc:
            errors.append(f"{path.name}:{line_number}: {exc.msg}")
            continue
        if not isinstance(value, dict):
            errors.append(f"{path.name}:{line_number}: expected a JSON object")
    return errors


def inspect(root: Path) -> dict[str, object]:
    result: dict[str, object] = {
        "workspace_path": str(root),
        "required_structure_version": STRUCTURE["structure_version"],
        "status": "project_not_found",
        "gate_passed": False,
        "missing": [],
        "invalid": [],
        "conflicts": [],
    }
    if root == Path(root.anchor) or root == Path.home().resolve():
        result["status"] = "unsafe_workspace"
        result["conflicts"].append("A filesystem root or home directory cannot be a project workspace")
        return result
    if root == DISTRIBUTION_ROOT or DISTRIBUTION_ROOT in root.parents:
        result["status"] = "plugin_source_workspace"
        result["conflicts"].append("The plugin distribution cannot be used as a user project workspace")
        return result
    if not root.is_dir():
        result["status"] = "workspace_not_found"
        result["conflicts"].append("The selected workspace directory does not exist")
        return result

    control = root / ".home-control"
    if is_linklike(control):
        result["status"] = "project_structure_invalid"
        result["conflicts"].append(".home-control: symbolic links and junctions are not allowed")
        return result
    marker = control / "project.json"
    if is_linklike(marker):
        result["status"] = "project_structure_invalid"
        result["conflicts"].append(".home-control/project.json: symbolic links and junctions are not allowed")
        return result
    if marker.exists() and not marker.is_file():
        result["status"] = "project_structure_invalid"
        result["conflicts"].append(".home-control/project.json: expected a file")
        return result
    if not marker.is_file():
        return result
    try:
        project = read_json(marker)
    except (OSError, json.JSONDecodeError) as exc:
        result["status"] = "project_structure_invalid"
        result["invalid"].append(f".home-control/project.json: {exc}")
        return result
    if not isinstance(project, dict):
        result["status"] = "project_structure_invalid"
        result["invalid"].append(".home-control/project.json: expected a JSON object")
        return result
    missing_identity = [
        field
        for field in ("schema_version", "project_id", "name")
        if not isinstance(project.get(field), str) or not project[field].strip()
    ]
    if missing_identity:
        result["status"] = "project_structure_invalid"
        result["invalid"].append(
            ".home-control/project.json: missing or invalid " + ", ".join(missing_identity)
        )
        return result

    folder_binding = project.get("folder_binding", {})
    created_by = project.get("created_by", {})
    if not isinstance(folder_binding, dict) or not isinstance(created_by, dict):
        result["status"] = "project_structure_invalid"
        result["invalid"].append(".home-control/project.json: folder_binding and created_by must be objects")
        return result
    bound = folder_binding.get("absolute_path") or project.get("project_root")
    try:
        bound_path = Path(bound).expanduser().resolve() if bound else None
    except (OSError, TypeError):
        bound_path = None
    if bound_path != root:
        result["status"] = "project_binding_mismatch"
        result["conflicts"].append("The project marker is bound to a different absolute path")
        return result
    if created_by.get("plugin_id") != STRUCTURE["plugin_id"]:
        result["status"] = "foreign_project_marker"
        result["conflicts"].append("The project marker was not created by this plugin")
        return result

    structure_version = created_by.get("structure_version")
    current_version = version_tuple(structure_version)
    required_version = version_tuple(STRUCTURE["structure_version"])
    if current_version is None or required_version is None:
        result["status"] = "project_structure_invalid"
        result["invalid"].append(".home-control/project.json: structure version is missing or invalid")
        return result
    result["project_structure_version"] = structure_version
    if current_version > required_version:
        result["status"] = "project_created_by_newer_plugin"
        result["conflicts"].append("The project structure is newer than this plugin can safely manage")
        return result
    migration_spec = SUPPORTED_MIGRATIONS.get(str(structure_version), {})
    migration_supported = (
        current_version < required_version
        and migration_spec.get("to") == STRUCTURE["structure_version"]
        and migration_spec.get("mode") == "additive"
    )
    if current_version < required_version and not migration_supported:
        result["status"] = "project_migration_unsupported"
        result["conflicts"].append(
            f"No supported migration from {structure_version} to {STRUCTURE['structure_version']}"
        )
        return result
    if current_version == required_version and structure_version != STRUCTURE["structure_version"]:
        result["status"] = "project_migration_unsupported"
        result["conflicts"].append("The project structure version has a non-canonical format")
        return result

    for relative in [*STRUCTURE["folders"], *STRUCTURE["control_directories"]]:
        target = root / relative
        if relative.startswith(".home-control") and is_linklike(target):
            result["conflicts"].append(f"{relative}: symbolic links and junctions are not allowed")
        elif not target.exists():
            result["missing"].append(relative)
        elif not target.is_dir():
            result["conflicts"].append(f"{relative}: expected a directory")

    for relative, expected_value in STRUCTURE["json_files"].items():
        target = root / relative
        if is_linklike(target):
            result["conflicts"].append(f"{relative}: symbolic links and junctions are not allowed")
        elif not target.exists():
            result["missing"].append(relative)
        elif not target.is_file():
            result["conflicts"].append(f"{relative}: expected a file")
        else:
            try:
                value = read_json(target)
                if relative.endswith("documents.json") and not isinstance(value.get("items"), list):
                    raise ValueError("items must be an array")
                expected_schema = expected_value.get("schema_version")
                actual_schema = value.get("schema_version")
                legacy_documents = (
                    migration_supported
                    and actual_schema == migration_spec.get("documents_schema_from")
                    and expected_schema == migration_spec.get("documents_schema_to")
                )
                if expected_schema and actual_schema != expected_schema and not legacy_documents:
                    raise ValueError(
                        f"schema_version {actual_schema!r} does not match required {expected_schema!r}"
                    )
            except (OSError, json.JSONDecodeError, AttributeError, ValueError) as exc:
                result["invalid"].append(f"{relative}: {exc}")

    for relative in STRUCTURE["jsonl_files"]:
        target = root / relative
        if is_linklike(target):
            result["conflicts"].append(f"{relative}: symbolic links and junctions are not allowed")
        elif not target.exists():
            result["missing"].append(relative)
        elif not target.is_file():
            result["conflicts"].append(f"{relative}: expected a file")
        else:
            result["invalid"].extend(inspect_jsonl(target))

    for relative, headers in STRUCTURE["csv_files"].items():
        target = root / relative
        if is_linklike(target):
            result["conflicts"].append(f"{relative}: symbolic links and junctions are not allowed")
        elif not target.exists():
            result["missing"].append(relative)
        elif not target.is_file():
            result["conflicts"].append(f"{relative}: expected a file")
        else:
            try:
                with target.open("r", encoding="utf-8-sig", newline="") as handle:
                    reader = csv.DictReader(handle)
                    actual = list(reader.fieldnames or [])
                    rows = list(reader)
                missing_headers = [header for header in headers if header not in actual]
                if missing_headers:
                    result["invalid"].append(f"{relative}: missing columns {', '.join(missing_headers)}")
                if len(set(actual)) != len(actual) or any(
                    None in row or any(value is None for value in row.values()) for row in rows
                ):
                    result["invalid"].append(f"{relative}: ambiguous columns or row widths")
            except (OSError, UnicodeError, csv.Error) as exc:
                result["invalid"].append(f"{relative}: {exc}")

    if result["invalid"]:
        result["status"] = "project_structure_invalid"
    elif result["conflicts"]:
        result["status"] = "project_structure_invalid"
    elif migration_supported:
        result["status"] = "project_migration_required"
    elif result["missing"]:
        result["status"] = "project_structure_incomplete"
    else:
        result["status"] = "existing_project_ready"
        result["gate_passed"] = True
    return result


def require_ready_project(path: Path) -> Path:
    root = path.expanduser().resolve()
    result = inspect(root)
    if not result["gate_passed"]:
        details = [*result["conflicts"], *result["invalid"], *result["missing"]]
        suffix = f": {'; '.join(str(item) for item in details[:5])}" if details else ""
        raise ValueError(f"Project gate failed with status {result['status']}{suffix}")
    return root


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("project_dir", type=Path)
    args = parser.parse_args()
    root = args.project_dir.expanduser().resolve()
    result = inspect(root)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["gate_passed"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
