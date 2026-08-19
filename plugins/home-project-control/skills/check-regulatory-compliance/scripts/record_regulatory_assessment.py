#!/usr/bin/env python3
"""Preview or append a validated normative-compliance package."""

from __future__ import annotations

import argparse
import json
import sys
import uuid
from pathlib import Path


SCRIPT_DIR = Path(__file__).resolve().parent
MANAGE_SCRIPTS = SCRIPT_DIR.parents[1] / "manage-project-evidence" / "scripts"
sys.path.insert(0, str(MANAGE_SCRIPTS))

from audit_project import STRUCTURE, audit, validate_regulatory_layer  # noqa: E402
from inspect_project import is_linklike, require_ready_project  # noqa: E402


PACKAGE_SCHEMA_VERSION = "1.0"
SECTIONS = {
    "norm_references": ("norm_references.jsonl", "norm_reference_id"),
    "regulatory_requirements": ("regulatory_requirements.jsonl", "regulatory_requirement_id"),
    "compliance_assessments": ("compliance_assessments.jsonl", "compliance_assessment_id"),
    "compliance_results": ("compliance_results.jsonl", "compliance_result_id"),
    "regulatory_sync_runs": ("regulatory_sync_runs.jsonl", "regulatory_sync_run_id"),
}


def load_jsonl(path: Path, id_field: str) -> tuple[list[dict], dict[str, dict]]:
    records: list[dict] = []
    by_id: dict[str, dict] = {}
    for line_number, raw in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if not raw.strip():
            continue
        value = json.loads(raw)
        if not isinstance(value, dict):
            raise ValueError(f"{path.name}:{line_number}: expected a JSON object")
        identifier = value.get(id_field)
        if not isinstance(identifier, str) or not identifier.strip():
            raise ValueError(f"{path.name}:{line_number}: missing {id_field}")
        identifier = identifier.strip()
        if identifier in by_id:
            raise ValueError(f"{path.name}:{line_number}: duplicate {id_field} {identifier}")
        records.append(value)
        by_id[identifier] = value
    return records, by_id


def load_package(path: Path) -> dict:
    if is_linklike(path) or not path.is_file():
        raise ValueError("Package path must be a regular non-linked JSON file")
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict) or value.get("schema_version") != PACKAGE_SCHEMA_VERSION:
        raise ValueError(f"Regulatory package schema_version must be {PACKAGE_SCHEMA_VERSION}")
    unknown = set(value) - {"schema_version", *SECTIONS}
    if unknown:
        raise ValueError("Unknown regulatory package sections: " + ", ".join(sorted(unknown)))
    return value


def active_documents(root: Path) -> set[str]:
    value = json.loads((root / ".home-control" / "documents.json").read_text(encoding="utf-8"))
    return {
        str(item.get("document_id", "")).strip()
        for item in value.get("items", [])
        if isinstance(item, dict) and item.get("status") == "active" and str(item.get("document_id", "")).strip()
    }


def merged_records(
    root: Path, additions: dict[str, list[dict]]
) -> tuple[dict[str, list[tuple[int, dict]]], dict[str, set[str]]]:
    records: dict[str, list[tuple[int, dict]]] = {}
    identifiers: dict[str, set[str]] = {}
    section_by_filename = {filename: section for section, (filename, _) in SECTIONS.items()}
    for relative, metadata in STRUCTURE["jsonl_files"].items():
        filename = Path(relative).name
        current, by_id = load_jsonl(root / relative, metadata["id_field"])
        section = section_by_filename.get(filename)
        appended = additions.get(section, [])
        combined = [*current, *appended]
        records[filename] = list(enumerate(combined, 1))
        identifiers[filename] = set(by_id) | {
            record[metadata["id_field"]] for record in appended
        }
    return records, identifiers


def validate_package(root: Path, package: dict) -> dict[str, list[dict]]:
    additions: dict[str, list[dict]] = {}
    for section, (filename, id_field) in SECTIONS.items():
        values = package.get(section, [])
        if not isinstance(values, list) or any(not isinstance(item, dict) for item in values):
            raise ValueError(f"Package section {section} must be an array of objects")
        _, existing = load_jsonl(root / ".home-control" / filename, id_field)
        additions[section] = []
        seen: set[str] = set()
        for number, record in enumerate(values, 1):
            identifier = record.get(id_field)
            if not isinstance(identifier, str) or not identifier.strip():
                raise ValueError(f"{section}[{number}] must have a non-empty {id_field}")
            identifier = identifier.strip()
            if identifier in seen:
                raise ValueError(f"Package section {section} repeats {id_field} {identifier}")
            seen.add(identifier)
            prior = existing.get(identifier)
            if prior is not None:
                if prior != record:
                    raise ValueError(f"Existing {id_field} {identifier} has different content")
                continue
            additions[section].append(record)

    empty = {section: [] for section in SECTIONS}
    existing_records, existing_ids = merged_records(root, empty)
    merged, merged_ids = merged_records(root, additions)
    documents = active_documents(root)
    existing_warnings: list[str] = []
    merged_warnings: list[str] = []
    validate_regulatory_layer(existing_records, existing_ids, documents, existing_warnings)
    validate_regulatory_layer(merged, merged_ids, documents, merged_warnings)
    introduced = sorted(set(merged_warnings) - set(existing_warnings))
    if introduced:
        raise ValueError("Regulatory package validation failed: " + "; ".join(introduced))
    return additions


def append_atomically(root: Path, additions: dict[str, list[dict]]) -> dict[str, int]:
    control = root / ".home-control"
    before_warnings = set(audit(root))
    staged: list[tuple[Path, Path, Path]] = []
    applied: list[tuple[Path, Path]] = []
    try:
        for section, records in additions.items():
            if not records:
                continue
            filename, _ = SECTIONS[section]
            target = control / filename
            if is_linklike(target) or not target.is_file():
                raise ValueError(f"Unsafe registry target: {filename}")
            temporary = target.with_name(f".{target.name}.{uuid.uuid4().hex}.tmp")
            backup = target.with_name(f".{target.name}.{uuid.uuid4().hex}.bak")
            existing = target.read_text(encoding="utf-8")
            suffix = "" if not existing or existing.endswith("\n") else "\n"
            appended = "".join(json.dumps(record, ensure_ascii=False) + "\n" for record in records)
            temporary.write_text(existing + suffix + appended, encoding="utf-8")
            staged.append((target, temporary, backup))
        for target, temporary, backup in staged:
            target.replace(backup)
            applied.append((target, backup))
            temporary.replace(target)
        introduced = sorted(set(audit(root)) - before_warnings)
        if introduced:
            raise ValueError("Applied package would introduce audit warnings: " + "; ".join(introduced))
        for _, backup in applied:
            backup.unlink()
    except Exception:
        for target, backup in reversed(applied):
            if target.exists() and not is_linklike(target):
                target.unlink()
            if backup.exists() and not is_linklike(backup):
                backup.replace(target)
        raise
    finally:
        for _, temporary, backup in staged:
            if temporary.exists() and not is_linklike(temporary):
                temporary.unlink()
            if backup.exists() and not is_linklike(backup):
                backup.unlink()
    return {section: len(records) for section, records in additions.items() if records}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("project_dir", type=Path)
    parser.add_argument("package", type=Path)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    root = require_ready_project(args.project_dir)
    additions = validate_package(root, load_package(args.package))
    result: dict[str, object] = {
        "mode": "preview",
        "append": {section: len(records) for section, records in additions.items()},
    }
    if args.apply:
        result = {"mode": "applied", "appended": append_atomically(root, additions)}
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (OSError, UnicodeError, ValueError, json.JSONDecodeError) as exc:
        print(f"Regulatory recording failed: {exc}", file=sys.stderr)
        raise SystemExit(2)
