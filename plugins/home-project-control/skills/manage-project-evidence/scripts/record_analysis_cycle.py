#!/usr/bin/env python3
"""Preview or append a validated fact-extraction, package and coordination package."""

from __future__ import annotations

import argparse
import json
import sys
import uuid
from pathlib import Path

from audit_project import (
    STRUCTURE,
    audit,
    complete_coverage_is_valid,
    normalized_unit_set,
    validate_analysis_layer,
    validate_context_layer,
    validate_fact_records,
)
from inspect_project import is_linklike, require_ready_project


PACKAGE_SCHEMA_VERSION = "1.0"
SECTIONS = {
    "facts": ("facts.jsonl", "fact_id"),
    "decisions": ("decisions.jsonl", "decision_id"),
    "project_packages": ("project_packages.jsonl", "package_id"),
    "fact_extraction_runs": ("fact_extraction_runs.jsonl", "extraction_run_id"),
    "information_gaps": ("information_gaps.jsonl", "gap_id"),
    "shared_resources": ("shared_resources.jsonl", "resource_id"),
    "resource_demands": ("resource_demands.jsonl", "demand_id"),
    "package_interfaces": ("package_interfaces.jsonl", "package_interface_id"),
    "coordination_issues": ("coordination_issues.jsonl", "coordination_issue_id"),
    "coordination_runs": ("coordination_runs.jsonl", "coordination_run_id"),
    "as_is_snapshots": ("as_is_snapshots.jsonl", "as_is_snapshot_id"),
    "analysis_requests": ("analysis_requests.jsonl", "analysis_request_id"),
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
        raise ValueError(f"Analysis package schema_version must be {PACKAGE_SCHEMA_VERSION}")
    unknown = set(value) - {"schema_version", *SECTIONS}
    if unknown:
        raise ValueError("Unknown analysis package sections: " + ", ".join(sorted(unknown)))
    return value


def document_context(
    root: Path,
) -> tuple[set[str], dict[str, set[tuple[object, str]]], dict[str, str]]:
    value = json.loads((root / ".home-control" / "documents.json").read_text(encoding="utf-8"))
    active: set[str] = set()
    versions: dict[str, set[tuple[object, str]]] = {}
    paths: dict[str, str] = {}
    for document in value.get("items", []):
        if not isinstance(document, dict):
            continue
        document_id = str(document.get("document_id", "")).strip()
        if not document_id:
            continue
        if document.get("status") == "active":
            active.add(document_id)
        paths[document_id] = str(document.get("relative_path", "")).replace("\\", "/").strip("/")
        versions[document_id] = {
            (item.get("version"), str(item.get("sha256", "")).strip())
            for item in document.get("versions", [])
            if isinstance(item, dict)
        }
    return active, versions, paths


def complete_read_versions(root: Path, records: dict[str, list[tuple[int, dict]]]) -> set[tuple[str, object, str]]:
    inventories: dict[tuple[str, object, str], dict] = {}
    for _, inventory in records["document_inventories.jsonl"]:
        if inventory.get("status") == "complete":
            key = (
                str(inventory.get("source_document_id", "")).strip(),
                inventory.get("document_version"),
                str(inventory.get("sha256", "")).strip(),
            )
            inventories[key] = inventory
    result: set[tuple[str, object, str]] = set()
    summaries_root = (root / ".home-control" / "summaries").resolve()
    for _, run in records["reading_runs.jsonl"]:
        if run.get("status") != "complete" or not complete_coverage_is_valid(run.get("coverage")):
            continue
        key = (
            str(run.get("source_document_id", "")).strip(),
            run.get("document_version"),
            str(run.get("sha256", "")).strip(),
        )
        inventory = inventories.get(key)
        coverage = run.get("coverage", {})
        if inventory is None or normalized_unit_set(coverage.get("expected_units")) != normalized_unit_set(
            inventory.get("expected_units")
        ):
            continue
        requirements = inventory.get("reading_requirements", [])
        checked_requirements = coverage.get("checked_requirements", [])
        if not isinstance(requirements, list) or not isinstance(checked_requirements, list) or set(requirements) != set(
            checked_requirements
        ):
            continue
        summary_path = str(run.get("summary_path", "")).strip()
        summary = root / summary_path if summary_path else None
        try:
            resolved = summary.resolve() if summary else None
            valid_summary = bool(
                resolved
                and summaries_root in resolved.parents
                and resolved.is_file()
                and summary is not None
                and not is_linklike(summary)
            )
        except (OSError, RuntimeError):
            valid_summary = False
        if valid_summary:
            result.add(key)
    return result


def merged_project_records(
    root: Path, additions: dict[str, list[dict]]
) -> tuple[dict[str, list[tuple[int, dict]]], dict[str, set[str]]]:
    records: dict[str, list[tuple[int, dict]]] = {}
    identifiers: dict[str, set[str]] = {}
    section_by_filename = {filename: key for key, (filename, _) in SECTIONS.items()}
    for relative, metadata in STRUCTURE["jsonl_files"].items():
        filename = Path(relative).name
        current, by_id = load_jsonl(root / relative, metadata["id_field"])
        section = section_by_filename.get(filename)
        combined = [*current, *(additions[section] if section else [])]
        records[filename] = list(enumerate(combined, 1))
        identifiers[filename] = set(by_id) | {
            record[metadata["id_field"]] for record in additions.get(section, [])
        }
    return records, identifiers


def validate_package(root: Path, package: dict) -> dict[str, list[dict]]:
    additions: dict[str, list[dict]] = {}
    for section, (filename, id_field) in SECTIONS.items():
        values = package.get(section, [])
        if not isinstance(values, list) or any(not isinstance(item, dict) for item in values):
            raise ValueError(f"Package section {section} must be an array of objects")
        _, existing = load_jsonl(root / ".home-control" / filename, id_field)
        seen: set[str] = set()
        additions[section] = []
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
    existing_records, existing_ids = merged_project_records(root, empty)
    merged_records, merged_ids = merged_project_records(root, additions)
    active_documents, document_versions, document_paths = document_context(root)
    existing_warnings: list[str] = []
    validate_fact_records(existing_records, existing_ids, active_documents, document_versions, existing_warnings)
    validate_analysis_layer(
        existing_records,
        existing_ids,
        active_documents,
        document_versions,
        complete_read_versions(root, existing_records),
        existing_warnings,
    )
    validate_context_layer(
        root,
        existing_records,
        existing_ids,
        active_documents,
        document_versions,
        document_paths,
        complete_read_versions(root, existing_records),
        existing_warnings,
    )
    merged_warnings: list[str] = []
    validate_fact_records(merged_records, merged_ids, active_documents, document_versions, merged_warnings)
    validate_analysis_layer(
        merged_records,
        merged_ids,
        active_documents,
        document_versions,
        complete_read_versions(root, merged_records),
        merged_warnings,
    )
    validate_context_layer(
        root,
        merged_records,
        merged_ids,
        active_documents,
        document_versions,
        document_paths,
        complete_read_versions(root, merged_records),
        merged_warnings,
    )
    introduced = sorted(set(merged_warnings) - set(existing_warnings))
    if introduced:
        raise ValueError("Analysis package validation failed: " + "; ".join(introduced))
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
            added = "".join(json.dumps(record, ensure_ascii=False) + "\n" for record in records)
            temporary.write_text(existing + suffix + added, encoding="utf-8")
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
        print(f"Analysis-cycle recording failed: {exc}", file=sys.stderr)
        raise SystemExit(2)
