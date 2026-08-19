#!/usr/bin/env python3
"""Preview or atomically append linked cost, schedule, baseline and control records."""

from __future__ import annotations

import argparse
import csv
import json
import shutil
import sys
import uuid
from datetime import date, datetime, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path


PLUGIN_ROOT = Path(__file__).resolve().parents[3]
MANAGE_SCRIPTS = PLUGIN_ROOT / "skills" / "manage-project-evidence" / "scripts"
sys.path.insert(0, str(MANAGE_SCRIPTS))
from inspect_project import is_linklike, require_ready_project  # noqa: E402
from management_model import REGISTRIES, validate_and_enrich  # noqa: E402


def read_jsonl(path: Path) -> list[dict]:
    if not path.is_file():
        return []
    records = []
    for line_number, raw in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if not raw.strip():
            continue
        value = json.loads(raw)
        if not isinstance(value, dict):
            raise ValueError(f"{path.name}:{line_number}: expected an object")
        records.append(value)
    return records


def registered_ids(control: Path) -> tuple[set[str], dict[str, set[str]]]:
    structure = json.loads((PLUGIN_ROOT / "schemas" / "project-structure.json").read_text(encoding="utf-8"))
    sources: set[str] = set()
    groups: dict[str, set[str]] = {
        "decisions": set(),
        "baseline_snapshots": set(),
        "project_packages": set(),
        "quote_items": set(),
        "price_observations": set(),
        "active_documents": set(),
    }
    documents = json.loads((control / "documents.json").read_text(encoding="utf-8"))
    for item in documents.get("items", []):
        if isinstance(item, dict) and item.get("status") == "active" and item.get("document_id"):
            document_id = str(item["document_id"]).strip()
            sources.add(document_id)
            groups["active_documents"].add(document_id)
    names = {
        "decisions.jsonl": "decisions",
        "baseline_snapshots.jsonl": "baseline_snapshots",
        "project_packages.jsonl": "project_packages",
        "quote_items.jsonl": "quote_items",
        "price_observations.jsonl": "price_observations",
    }
    for relative, metadata in structure["jsonl_files"].items():
        filename = Path(relative).name
        for record in read_jsonl(control / filename):
            identifier = str(record.get(metadata["id_field"], "")).strip()
            if identifier:
                sources.add(identifier)
                if filename in names:
                    groups[names[filename]].add(identifier)
    return sources, groups


def csv_ids(path: Path, id_field: str) -> set[str]:
    if not path.is_file():
        return set()
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        return {
            row.get(id_field, "").strip()
            for row in csv.DictReader(handle)
            if row.get(id_field, "").strip()
        }


def csv_ids_with_status(path: Path, id_field: str, status: str) -> set[str]:
    if not path.is_file():
        return set()
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        return {
            row.get(id_field, "").strip()
            for row in csv.DictReader(handle)
            if row.get(id_field, "").strip() and row.get("status", "").strip() == status
        }


def csv_rows(path: Path, id_field: str) -> dict[str, dict[str, str]]:
    if not path.is_file():
        return {}
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        return {
            row.get(id_field, "").strip(): row
            for row in csv.DictReader(handle)
            if row.get(id_field, "").strip()
        }


def confirmed_actual_cost(
    control: Path,
    currency: str,
    work_item_ids: set[str],
    data_date: date,
) -> tuple[Decimal, list[str]]:
    total = Decimal("0")
    selected_ids: list[str] = []
    documents = json.loads((control / "documents.json").read_text(encoding="utf-8"))
    active_documents = {
        str(item.get("document_id", "")).strip()
        for item in documents.get("items", [])
        if isinstance(item, dict) and item.get("status") == "active"
    }
    with (control / "costs.csv").open("r", encoding="utf-8-sig", newline="") as handle:
        for row in csv.DictReader(handle):
            if row.get("status", "").strip() != "confirmed_paid":
                continue
            if row.get("currency", "").strip() != currency:
                continue
            if row.get("work_item_id", "").strip() not in work_item_ids:
                continue
            try:
                if date.fromisoformat(row.get("date", "").strip()) > data_date:
                    continue
            except ValueError:
                continue
            if row.get("evidence_document_id", "").strip() not in active_documents or not row.get("evidence_locator", "").strip():
                continue
            try:
                total += Decimal(row.get("amount", "").replace(" ", "").replace(",", "."))
                selected_ids.append(row.get("cost_id", "").strip())
            except (InvalidOperation, AttributeError):
                continue
    return total, sorted(selected_ids)


def atomic_write_jsonl(path: Path, records: list[dict]) -> None:
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    try:
        text = "".join(json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n" for record in records)
        temporary.write_text(text, encoding="utf-8")
        temporary.replace(path)
    finally:
        if temporary.exists():
            temporary.unlink()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("project_dir", type=Path)
    parser.add_argument("package", type=Path)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    root = require_ready_project(args.project_dir)
    control = root / ".home-control"
    package_path = args.package.expanduser().resolve()
    if is_linklike(package_path) or not package_path.is_file():
        raise ValueError("Package must be an existing regular JSON file")
    package = json.loads(package_path.read_text(encoding="utf-8"))
    if not isinstance(package, dict) or package.get("schema_version") != "1.0":
        raise ValueError("Management package must be an object with schema_version 1.0")
    unknown_keys = set(package) - {"schema_version", *REGISTRIES}
    if unknown_keys:
        raise ValueError("Unknown management package keys: " + ", ".join(sorted(unknown_keys)))

    existing: dict[str, list[dict]] = {}
    incoming: dict[str, list[dict]] = {}
    for key, (filename, id_field) in REGISTRIES.items():
        target = control / filename
        if is_linklike(target):
            raise ValueError(f"Refusing linked registry {filename}")
        existing[key] = read_jsonl(target)
        value = package.get(key, [])
        if not isinstance(value, list):
            raise ValueError(f"{key} must be an array")
        existing_ids = {str(record.get(id_field, "")).strip() for record in existing[key]}
        incoming_ids = [str(record.get(id_field, "")).strip() for record in value if isinstance(record, dict)]
        conflicts = sorted(identifier for identifier in incoming_ids if identifier and identifier in existing_ids)
        if conflicts:
            raise ValueError(f"{key} attempts to replace append-only IDs: {', '.join(conflicts)}")
        incoming[key] = value

    sources, groups = registered_ids(control)
    context = {
        "sources": sources,
        "decisions": groups["decisions"],
        "baseline_snapshots": groups["baseline_snapshots"],
        "project_packages": groups["project_packages"],
        "quote_items": groups["quote_items"],
        "price_observations": groups["price_observations"],
        "work_items": csv_ids(control / "work_items.csv", "work_item_id"),
        "changes": csv_ids(control / "changes.csv", "change_id"),
        "approved_changes": csv_ids_with_status(control / "changes.csv", "change_id", "approved"),
        "cost_rows": csv_rows(control / "costs.csv", "cost_id"),
        "active_documents": groups["active_documents"],
    }
    context["sources"].update(context["work_items"])
    context["sources"].update(context["changes"])
    context["sources"].update(context["cost_rows"])
    combined = {key: [*existing[key], *incoming[key]] for key in REGISTRIES}
    raw_cost_plans = {
        str(record.get("cost_plan_id", "")).strip(): record
        for record in combined["cost_plans"]
        if isinstance(record, dict)
    }
    raw_baselines = {
        str(record.get("management_baseline_id", "")).strip(): record
        for record in combined["management_baselines"]
        if isinstance(record, dict)
    }
    for snapshot in incoming["control_snapshots"]:
        if not isinstance(snapshot, dict):
            continue
        baseline = raw_baselines.get(str(snapshot.get("management_baseline_id", "")).strip())
        cost = raw_cost_plans.get(str(baseline.get("cost_plan_id", "")).strip()) if baseline else None
        if cost and str(cost.get("currency", "")).strip():
            scoped_work_items = {
                str(item.get("work_item_id", "")).strip()
                for item in cost.get("items", [])
                if isinstance(item, dict) and str(item.get("work_item_id", "")).strip()
            }
            try:
                cutoff = date.fromisoformat(str(snapshot.get("data_date", "")))
            except (TypeError, ValueError):
                cutoff = date.min
            actual, cost_ids = confirmed_actual_cost(
                control, str(cost["currency"]).strip(), scoped_work_items, cutoff
            )
            snapshot["confirmed_actual_cost"] = float(actual)
            snapshot["confirmed_actual_cost_ids"] = cost_ids
    enriched, errors = validate_and_enrich(combined, context)
    if errors:
        raise ValueError("Management package is invalid:\n- " + "\n- ".join(errors))

    incoming_enriched: dict[str, list[dict]] = {}
    for key, (_, id_field) in REGISTRIES.items():
        requested_ids = {str(record.get(id_field, "")).strip() for record in incoming[key]}
        incoming_enriched[key] = [
            record for record in enriched[key] if str(record.get(id_field, "")).strip() in requested_ids
        ]

    cost_plans = {record["cost_plan_id"]: record for record in enriched["cost_plans"]}
    baselines = {record["management_baseline_id"]: record for record in enriched["management_baselines"]}
    for snapshot in incoming_enriched["control_snapshots"]:
        baseline = baselines[snapshot["management_baseline_id"]]
        cost = cost_plans[baseline["cost_plan_id"]]
        scoped_work_items = {
            str(item.get("work_item_id", "")).strip()
            for item in cost.get("items", [])
            if isinstance(item, dict) and str(item.get("work_item_id", "")).strip()
        }
        cutoff = date.fromisoformat(str(snapshot["data_date"]))
        actual, cost_ids = confirmed_actual_cost(
            control, cost["currency"], scoped_work_items, cutoff
        )
        snapshot["confirmed_actual_cost"] = float(actual)
        snapshot["confirmed_actual_cost_ids"] = cost_ids
    if incoming_enriched["control_snapshots"]:
        recombined = {
            key: [*existing[key], *incoming_enriched[key]]
            for key in REGISTRIES
        }
        enriched, errors = validate_and_enrich(recombined, context)
        if errors:
            raise ValueError("Management package is invalid after actual-cost calculation:\n- " + "\n- ".join(errors))
        for key, (_, id_field) in REGISTRIES.items():
            requested_ids = {str(record.get(id_field, "")).strip() for record in incoming[key]}
            incoming_enriched[key] = [
                record for record in enriched[key] if str(record.get(id_field, "")).strip() in requested_ids
            ]

    plan = {
        "mode": "apply" if args.apply else "preview",
        "project_root": str(root),
        "records_to_append": {key: len(value) for key, value in incoming_enriched.items()},
        "calculated_cost_totals": {
            value["cost_plan_id"]: value.get("total_amount")
            for value in incoming_enriched["cost_plans"]
        },
        "calculated_schedule_finishes": {
            value["schedule_plan_id"]: value.get("calculated_finish")
            for value in incoming_enriched["schedule_plans"]
        },
        "control_metrics": {
            value["control_snapshot_id"]: value.get("metrics")
            for value in incoming_enriched["control_snapshots"]
        },
    }
    print(json.dumps(plan, ensure_ascii=False, indent=2))
    if not args.apply:
        return 0

    touched = [key for key, value in incoming_enriched.items() if value]
    if not touched:
        return 0
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S.%fZ") + "-" + uuid.uuid4().hex
    backup_root = control / "recovery" / stamp / "management-cycle"
    if is_linklike(control / "recovery"):
        raise ValueError("Unsafe recovery path")
    backups: dict[Path, Path] = {}
    try:
        for key in touched:
            filename, _ = REGISTRIES[key]
            target = control / filename
            backup = backup_root / filename
            backup.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(target, backup)
            backups[target] = backup
        for key in touched:
            filename, _ = REGISTRIES[key]
            atomic_write_jsonl(control / filename, [*existing[key], *incoming_enriched[key]])
    except Exception:
        for target, backup in backups.items():
            if backup.is_file():
                shutil.copy2(backup, target)
        raise
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (OSError, UnicodeError, ValueError, RuntimeError, json.JSONDecodeError) as exc:
        print(f"Management-cycle recording failed: {exc}", file=sys.stderr)
        raise SystemExit(2)
