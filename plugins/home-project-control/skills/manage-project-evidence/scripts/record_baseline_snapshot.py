#!/usr/bin/env python3
"""Preview or atomically record an owner-accepted versioned project baseline."""

from __future__ import annotations

import argparse
import json
import shutil
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path

from audit_project import (
    DIMENSIONS,
    audit,
    complete_coverage_is_valid,
    normalized_unit_set,
    validate_baseline_snapshots,
)
from inspect_project import is_linklike, require_ready_project


PACKAGE_SCHEMA_VERSION = "1.0"
REGISTRIES = {
    "facts": ("facts.jsonl", "fact_id"),
    "decisions": ("decisions.jsonl", "decision_id"),
    "approved_requirements": ("approved_requirements.jsonl", "requirement_id"),
    "baseline_snapshots": ("baseline_snapshots.jsonl", "baseline_snapshot_id"),
}


def utc_stamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S.%fZ") + "-" + uuid.uuid4().hex


def read_jsonl(path: Path, id_field: str) -> tuple[list[dict], dict[str, dict]]:
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


def string_list(value: object, location: str, allow_empty: bool = True) -> list[str]:
    if not isinstance(value, list):
        raise ValueError(f"{location} must be an array")
    result: list[str] = []
    for item in value:
        if not isinstance(item, str) or not item.strip():
            raise ValueError(f"{location} contains an empty or non-string value")
        result.append(item.strip())
    if not allow_empty and not result:
        raise ValueError(f"{location} must not be empty")
    if len(result) != len(set(result)):
        raise ValueError(f"{location} contains duplicates")
    return result


def load_package(path: Path) -> dict:
    if is_linklike(path) or not path.is_file():
        raise ValueError("Package path must be a regular non-linked JSON file")
    package = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(package, dict):
        raise ValueError("Baseline package must be a JSON object")
    if package.get("schema_version") != PACKAGE_SCHEMA_VERSION:
        raise ValueError(f"Baseline package schema_version must be {PACKAGE_SCHEMA_VERSION}")
    unknown = set(package) - {"schema_version", *REGISTRIES}
    if unknown:
        raise ValueError("Unknown baseline package sections: " + ", ".join(sorted(unknown)))
    return package


def indexed_versions(root: Path) -> tuple[set[str], dict[str, set[tuple[object, str]]], set[str]]:
    documents = json.loads((root / ".home-control" / "documents.json").read_text(encoding="utf-8"))
    active: set[str] = set()
    versions: dict[str, set[tuple[object, str]]] = {}
    proposal_documents: set[str] = set()
    for item in documents.get("items", []):
        if not isinstance(item, dict):
            continue
        document_id = str(item.get("document_id", "")).strip()
        if not document_id:
            continue
        if item.get("status") == "active":
            active.add(document_id)
        relative_path = str(item.get("relative_path", "")).replace("\\", "/").strip("/")
        if relative_path.split("/", 1)[0].casefold() == "03_Коммерческие_предложения".casefold():
            proposal_documents.add(document_id)
        history = item.get("versions", [])
        if isinstance(history, list):
            versions[document_id] = {
                (entry.get("version"), str(entry.get("sha256", "")).strip())
                for entry in history
                if isinstance(entry, dict)
            }
    return active, versions, proposal_documents


def complete_read_versions(root: Path, active_documents: set[str]) -> set[tuple[str, object, str]]:
    _, inventories = read_jsonl(root / ".home-control" / "document_inventories.jsonl", "inventory_id")
    runs, _ = read_jsonl(root / ".home-control" / "reading_runs.jsonl", "reading_run_id")
    summaries_root = (root / ".home-control" / "summaries").resolve()
    result: set[tuple[str, object, str]] = set()
    for run in runs:
        if run.get("status") != "complete":
            continue
        document_id = str(run.get("source_document_id", "")).strip()
        version = run.get("document_version")
        sha256 = str(run.get("sha256", "")).strip()
        inventory = next(
            (
                value
                for value in inventories.values()
                if value.get("source_document_id") == document_id
                and value.get("document_version") == version
                and value.get("sha256") == sha256
                and value.get("status") == "complete"
            ),
            None,
        )
        coverage = run.get("coverage")
        summary_path = str(run.get("summary_path", "")).strip()
        summary = root / summary_path if summary_path else None
        try:
            resolved = summary.resolve() if summary else None
            summary_valid = bool(
                resolved
                and summaries_root in resolved.parents
                and resolved.is_file()
                and summary is not None
                and not is_linklike(summary)
            )
        except (OSError, RuntimeError):
            summary_valid = False
        requirements = inventory.get("reading_requirements", []) if inventory else None
        checked = coverage.get("checked_requirements", []) if isinstance(coverage, dict) else None
        requirements_valid = bool(
            isinstance(requirements, list)
            and isinstance(checked, list)
            and all(isinstance(value, str) and value.strip() for value in requirements)
            and all(isinstance(value, str) and value.strip() for value in checked)
            and len(checked) == len(set(checked))
            and set(checked) == set(requirements)
        )
        if (
            document_id in active_documents
            and inventory is not None
            and complete_coverage_is_valid(coverage)
            and normalized_unit_set(coverage.get("expected_units")) == normalized_unit_set(inventory.get("expected_units"))
            and requirements_valid
            and summary_valid
        ):
            result.add((document_id, version, sha256))
    return result


def validate_package(root: Path, package: dict) -> dict[str, list[dict]]:
    additions: dict[str, list[dict]] = {}
    existing_lists: dict[str, list[dict]] = {}
    existing_by_key: dict[str, dict[str, dict]] = {}
    for key, (filename, id_field) in REGISTRIES.items():
        existing_list, existing = read_jsonl(root / ".home-control" / filename, id_field)
        existing_lists[key] = existing_list
        existing_by_key[key] = existing
        values = package.get(key, [])
        if not isinstance(values, list) or any(not isinstance(value, dict) for value in values):
            raise ValueError(f"Package section {key} must be an array of objects")
        seen: set[str] = set()
        additions[key] = []
        for number, record in enumerate(values, 1):
            identifier = record.get(id_field)
            if not isinstance(identifier, str) or not identifier.strip():
                raise ValueError(f"{key}[{number}] must have a non-empty {id_field}")
            identifier = identifier.strip()
            if identifier in seen:
                raise ValueError(f"Package section {key} repeats {id_field} {identifier}")
            seen.add(identifier)
            if identifier in existing:
                raise ValueError(f"Refusing to replace existing {id_field} {identifier}")
            additions[key].append(record)

    if len(additions["decisions"]) != 1 or len(additions["baseline_snapshots"]) != 1:
        raise ValueError("A baseline package must contain exactly one new decision and one new snapshot")
    if not additions["facts"] or not additions["approved_requirements"]:
        raise ValueError("A baseline package must contain source facts and atomic approved requirements")

    active_documents, document_versions, proposal_document_ids = indexed_versions(root)
    complete_versions = complete_read_versions(root, active_documents)
    known_facts = {**existing_by_key["facts"], **{value["fact_id"]: value for value in additions["facts"]}}
    for fact in additions["facts"]:
        fact_id = fact["fact_id"]
        for field in ("statement_kind", "evidence_origin", "verification_status"):
            if fact.get(field) not in DIMENSIONS[field]:
                raise ValueError(f"Fact {fact_id} has an unknown or missing {field}")
        if not str(fact.get("statement", "")).strip() or not str(fact.get("locator", "")).strip():
            raise ValueError(f"Fact {fact_id} requires an atomic statement and precise locator")
        document_id = str(fact.get("source_document_id", "")).strip()
        if document_id:
            version_key = (fact.get("document_version"), str(fact.get("sha256", "")).strip())
            if document_id not in active_documents or version_key not in document_versions.get(document_id, set()):
                raise ValueError(f"Fact {fact_id} is not bound to an active indexed document version")
        elif fact.get("evidence_origin") != "owner_confirmation":
            raise ValueError(f"Fact {fact_id} without a document must be explicit owner confirmation")

    decision = additions["decisions"][0]
    if (
        decision.get("decision_type") != "baseline_acceptance"
        or decision.get("status") != "approved"
        or decision.get("approved_by") != "owner"
        or not str(decision.get("approved_at", "")).strip()
        or not str(decision.get("decision", "")).strip()
    ):
        raise ValueError("Baseline decision must be explicitly approved by the owner and timestamped")
    decision_fact_ids = string_list(decision.get("source_fact_ids", []), "decision.source_fact_ids")
    if any(value not in known_facts for value in decision_fact_ids):
        raise ValueError("Baseline decision refers to an unknown fact")

    snapshot = additions["baseline_snapshots"][0]
    if snapshot.get("accepted_at") != decision.get("approved_at"):
        raise ValueError("Baseline snapshot accepted_at must match the owner decision approved_at")
    snapshot_id = snapshot["baseline_snapshot_id"]
    for requirement in additions["approved_requirements"]:
        requirement_id = requirement["requirement_id"]
        if not str(requirement.get("statement", "")).strip() or not str(requirement.get("scope", "")).strip():
            raise ValueError(f"ApprovedRequirement {requirement_id} requires one atomic statement and scope")
        if requirement.get("baseline_status") != "approved":
            raise ValueError(f"ApprovedRequirement {requirement_id} must be owner-approved")
        if requirement.get("verification_status") != "verified":
            raise ValueError(f"ApprovedRequirement {requirement_id} must be verified")
        if requirement.get("decision_id") != decision["decision_id"]:
            raise ValueError(f"ApprovedRequirement {requirement_id} is not linked to the owner decision")
        if requirement.get("baseline_snapshot_id") != snapshot_id:
            raise ValueError(f"ApprovedRequirement {requirement_id} is not linked to the snapshot")
        fact_ids = string_list(
            requirement.get("source_fact_ids", []),
            f"ApprovedRequirement {requirement_id}.source_fact_ids",
            allow_empty=False,
        )
        if any(value not in known_facts for value in fact_ids):
            raise ValueError(f"ApprovedRequirement {requirement_id} refers to an unknown fact")

    merged_records = {
        "facts.jsonl": list(enumerate([*existing_lists["facts"], *additions["facts"]], 1)),
        "decisions.jsonl": list(enumerate([*existing_lists["decisions"], *additions["decisions"]], 1)),
        "approved_requirements.jsonl": list(
            enumerate([*existing_lists["approved_requirements"], *additions["approved_requirements"]], 1)
        ),
        "baseline_snapshots.jsonl": list(
            enumerate([*existing_lists["baseline_snapshots"], *additions["baseline_snapshots"]], 1)
        ),
        "quotes.jsonl": list(
            enumerate(read_jsonl(root / ".home-control" / "quotes.jsonl", "quote_id")[0], 1)
        ),
    }
    merged_ids = {
        filename: {
            str(record.get(id_field, "")).strip()
            for _, record in records
            if str(record.get(id_field, "")).strip()
        }
        for filename, id_field, records in (
            ("facts.jsonl", "fact_id", merged_records["facts.jsonl"]),
            ("decisions.jsonl", "decision_id", merged_records["decisions.jsonl"]),
            ("approved_requirements.jsonl", "requirement_id", merged_records["approved_requirements.jsonl"]),
            ("baseline_snapshots.jsonl", "baseline_snapshot_id", merged_records["baseline_snapshots.jsonl"]),
            ("quotes.jsonl", "quote_id", merged_records["quotes.jsonl"]),
        )
    }
    baseline_warnings: list[str] = []
    current_ids = validate_baseline_snapshots(
        merged_records,
        merged_ids,
        document_versions,
        complete_versions,
        proposal_document_ids,
        baseline_warnings,
    )
    if baseline_warnings:
        raise ValueError("Baseline validation failed: " + "; ".join(baseline_warnings[:10]))
    if snapshot_id not in current_ids:
        raise ValueError("The new snapshot must be the current baseline")
    return additions


def write_registry_atomic(path: Path, records: list[dict]) -> None:
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    try:
        temporary.write_text(
            "".join(json.dumps(record, ensure_ascii=False) + "\n" for record in records),
            encoding="utf-8",
        )
        temporary.replace(path)
    finally:
        if temporary.exists():
            temporary.unlink()


def apply_package(root: Path, additions: dict[str, list[dict]]) -> dict[str, int]:
    before_warnings = set(audit(root))
    originals: dict[Path, bytes] = {}
    recovery_root = root / ".home-control" / "recovery" / utc_stamp() / "baseline-package"
    recovery = root / ".home-control" / "recovery"
    if is_linklike(recovery) or (recovery.exists() and not recovery.is_dir()):
        raise ValueError("Unsafe .home-control/recovery path")
    try:
        for key, records in additions.items():
            if not records:
                continue
            filename, id_field = REGISTRIES[key]
            path = root / ".home-control" / filename
            originals[path] = path.read_bytes()
            backup = recovery_root / filename
            backup.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(path, backup)
            existing = read_jsonl(path, id_field)[0]
            write_registry_atomic(path, [*existing, *records])
        new_warnings = set(audit(root)) - before_warnings
        if new_warnings:
            raise ValueError("Package created new audit warnings: " + "; ".join(sorted(new_warnings)[:10]))
    except Exception:
        for path, content in originals.items():
            temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.rollback")
            try:
                temporary.write_bytes(content)
                temporary.replace(path)
            finally:
                if temporary.exists():
                    temporary.unlink()
        raise
    return {key: len(records) for key, records in additions.items() if records}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("project_dir", type=Path)
    parser.add_argument("package_json", type=Path)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    root = require_ready_project(args.project_dir)
    package = load_package(args.package_json.expanduser())
    additions = validate_package(root, package)
    plan = {key: len(records) for key, records in additions.items() if records}
    result: dict[str, object] = {"mode": "preview", "project_root": str(root), "append": plan}
    if args.apply:
        result = {"mode": "applied", "project_root": str(root), "appended": apply_package(root, additions)}
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (OSError, UnicodeError, ValueError, json.JSONDecodeError) as exc:
        print(f"Baseline package failed: {exc}", file=sys.stderr)
        raise SystemExit(2)
