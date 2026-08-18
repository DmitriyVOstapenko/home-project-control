#!/usr/bin/env python3
"""Audit local project registries without changing source documents."""

from __future__ import annotations

import argparse
import csv
import json
import uuid
from pathlib import Path

from inspect_project import require_ready_project


PLUGIN_ROOT = Path(__file__).resolve().parents[3]
ONTOLOGY = json.loads((PLUGIN_ROOT / "schemas" / "ontology.json").read_text(encoding="utf-8"))
DIMENSIONS = {name: set(values) for name, values in ONTOLOGY["dimensions"].items()}
STRUCTURE = json.loads((PLUGIN_ROOT / "schemas" / "project-structure.json").read_text(encoding="utf-8"))


CSV_IDS = {
    "costs.csv": "cost_id",
    "work_items.csv": "work_item_id",
    "issues.csv": "issue_id",
    "changes.csv": "change_id",
    "commitments.csv": "commitment_id",
    "acceptance.csv": "acceptance_id",
    "procurement.csv": "procurement_id",
}

JSONL_IDS = {
    Path(relative).name: metadata["id_field"]
    for relative, metadata in STRUCTURE["jsonl_files"].items()
}


# Minimal fields that make the physical-object graph auditable rather than
# merely well-linked. Relationship targets are checked below; this table keeps
# the provenance and identity requirements from physical-object-model.md in
# one declarative place.
PHYSICAL_RECORD_REQUIREMENTS = {
    "sites.jsonl": {
        "text": ("project_id", "name", "site_kind", "status"),
        "arrays": ("source_fact_ids",),
    },
    "zones.jsonl": {
        "text": ("site_id", "name", "zone_kind"),
        "arrays": ("source_fact_ids",),
    },
    "physical_elements.jsonl": {
        "text": ("zone_id", "element_type", "locator"),
        "arrays": ("source_fact_ids",),
    },
    "systems.jsonl": {
        "text": ("name", "function", "operational_status"),
        "arrays": ("site_ids", "source_fact_ids"),
    },
    "assets.jsonl": {
        "text": ("site_id", "asset_type", "name", "lifecycle_status", "operational_status"),
        "arrays": ("source_fact_ids",),
        "arrays_allow_empty": ("system_ids",),
    },
    "asset_interfaces.jsonl": {
        "text": ("interface_type",),
        "arrays": ("endpoint_entity_ids", "source_fact_ids"),
    },
    "routes.jsonl": {
        "text": ("site_id", "route_type", "locator"),
        "arrays": ("zone_ids", "system_ids", "source_fact_ids"),
    },
    "asset_events.jsonl": {
        "text": ("event_type", "description"),
        "arrays": ("asset_ids", "source_fact_ids"),
    },
    "condition_assessments.jsonl": {
        "text": ("target_entity_id", "method", "condition_status"),
        "arrays": ("source_fact_ids",),
    },
    "maintenance_plans.jsonl": {
        "text": ("operation", "trigger_type", "status"),
        "arrays": ("target_entity_ids",),
        "evidence_any": ("basis_fact_ids", "norm_reference_ids"),
    },
    "work_requests.jsonl": {
        "text": ("request_type", "goal", "status"),
        "arrays": ("target_entity_ids", "source_fact_ids"),
    },
}


def validate_project(path: Path) -> Path:
    return require_ready_project(path)


def read_csv(path: Path) -> list[dict[str, str]]:
    if not path.is_file():
        return []
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))


def read_jsonl_records(path: Path, id_field: str, warnings: list[str]) -> tuple[list[tuple[int, dict]], set[str]]:
    records: list[tuple[int, dict]] = []
    identifiers: set[str] = set()
    if not path.is_file():
        return records, identifiers
    for line_number, raw in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if not raw.strip():
            continue
        try:
            record = json.loads(raw)
        except json.JSONDecodeError as exc:
            warnings.append(f"{path.name}:{line_number}: invalid JSON: {exc.msg}")
            continue
        if not isinstance(record, dict):
            warnings.append(f"{path.name}:{line_number}: expected a JSON object")
            continue
        value = str(record.get(id_field, "")).strip()
        if not value:
            warnings.append(f"{path.name}:{line_number}: missing {id_field}")
        elif value in identifiers:
            warnings.append(f"{path.name}:{line_number}: duplicate {id_field} {value}")
        if value:
            identifiers.add(value)
        records.append((line_number, record))
    return records, identifiers


def id_list(record: dict, field: str, location: str, warnings: list[str]) -> list[str]:
    value = record.get(field, [])
    if value is None:
        return []
    if not isinstance(value, list):
        warnings.append(f"{location}: {field} must be an array")
        return []
    result: list[str] = []
    for item in value:
        if not isinstance(item, str) or not item.strip():
            warnings.append(f"{location}: {field} contains an invalid identifier")
            continue
        result.append(item.strip())
    return result


def validate_physical_record_requirements(
    jsonl_records: dict[str, list[tuple[int, dict]]], warnings: list[str]
) -> None:
    for filename, rules in PHYSICAL_RECORD_REQUIREMENTS.items():
        for line_number, record in jsonl_records.get(filename, []):
            location = f"{filename}:{line_number}"
            for field in rules.get("text", ()):
                value = record.get(field)
                if not isinstance(value, str) or not value.strip():
                    warnings.append(f"{location}: missing required field {field}")
            for field in rules.get("arrays", ()):
                value = record.get(field)
                if not isinstance(value, list) or not value:
                    warnings.append(f"{location}: missing or empty required array {field}")
            for field in rules.get("arrays_allow_empty", ()):
                if not isinstance(record.get(field), list):
                    warnings.append(f"{location}: required field {field} must be an array")
            evidence_fields = rules.get("evidence_any", ())
            if evidence_fields and not any(
                isinstance(record.get(field), list) and bool(record[field])
                for field in evidence_fields
            ):
                warnings.append(
                    f"{location}: at least one evidence array is required: "
                    + " or ".join(evidence_fields)
                )
            verification_status = record.get("verification_status")
            if verification_status not in DIMENSIONS["verification_status"]:
                warnings.append(f"{location}: missing or unknown verification_status")


def complete_coverage_is_valid(coverage: object) -> bool:
    if not isinstance(coverage, dict):
        return False
    expected = coverage.get("expected_units")
    checked = coverage.get("checked_units")
    gaps = coverage.get("gaps")
    if not isinstance(expected, list) or not isinstance(checked, list) or not isinstance(gaps, list):
        return False
    if not expected or gaps:
        return False

    def normalized_units(units: list[object]) -> list[str] | None:
        normalized: list[str] = []
        for unit in units:
            if isinstance(unit, bool) or not isinstance(unit, (int, str)):
                return None
            if isinstance(unit, str) and not unit.strip():
                return None
            normalized.append(json.dumps(unit, ensure_ascii=False, sort_keys=True))
        return normalized

    expected_units = normalized_units(expected)
    checked_units = normalized_units(checked)
    if expected_units is None or checked_units is None:
        return False
    if len(expected_units) != len(set(expected_units)) or len(checked_units) != len(set(checked_units)):
        return False
    return set(expected_units) == set(checked_units)


def records_by_id(records: list[tuple[int, dict]], id_field: str) -> dict[str, dict]:
    result: dict[str, dict] = {}
    for _, record in records:
        value = str(record.get(id_field, "")).strip()
        if value and value not in result:
            result[value] = record
    return result


def document_ok(row: dict[str, str], field: str, active_documents: set[str]) -> bool:
    value = row.get(field, "").strip()
    return bool(value) and value in active_documents


def audit(root: Path) -> list[str]:
    control = root / ".home-control"
    warnings: list[str] = []
    project = json.loads((control / "project.json").read_text(encoding="utf-8"))
    project_id = str(project.get("project_id", "")).strip()
    documents = json.loads((control / "documents.json").read_text(encoding="utf-8"))
    active_documents: set[str] = set()
    document_versions: dict[str, set[tuple[object, str]]] = {}
    seen_document_ids: set[str] = set()
    for item_number, item in enumerate(documents.get("items", []), 1):
        if not isinstance(item, dict):
            warnings.append(f"documents.json:item {item_number}: expected a JSON object")
            continue
        document_id = str(item.get("document_id", "")).strip()
        if not document_id:
            warnings.append(f"documents.json:item {item_number}: missing document_id")
            continue
        if document_id in seen_document_ids:
            warnings.append(f"documents.json:item {item_number}: duplicate document_id {document_id}")
        seen_document_ids.add(document_id)
        if item.get("status") == "active":
            active_documents.add(document_id)
        versions: set[tuple[object, str]] = set()
        raw_versions = item.get("versions", [])
        if not isinstance(raw_versions, list):
            warnings.append(f"documents.json:item {item_number}: versions must be an array")
        else:
            for version in raw_versions:
                if not isinstance(version, dict):
                    warnings.append(f"documents.json:item {item_number}: version entry must be an object")
                    continue
                version_number = version.get("version")
                version_sha = str(version.get("sha256", "")).strip()
                if not isinstance(version_number, int) or not version_sha:
                    warnings.append(f"documents.json:item {item_number}: invalid version number or sha256")
                    continue
                versions.add((version_number, version_sha))
        document_versions[document_id] = versions
    jsonl_records: dict[str, list[tuple[int, dict]]] = {}
    jsonl_ids: dict[str, set[str]] = {}
    for filename, id_field in JSONL_IDS.items():
        records, identifiers = read_jsonl_records(control / filename, id_field, warnings)
        jsonl_records[filename] = records
        jsonl_ids[filename] = identifiers
    validate_physical_record_requirements(jsonl_records, warnings)
    decisions = jsonl_ids["decisions.jsonl"]

    tables: dict[str, list[dict[str, str]]] = {}
    for filename, id_field in CSV_IDS.items():
        rows = read_csv(control / filename)
        tables[filename] = rows
        seen: set[str] = set()
        for line_number, row in enumerate(rows, 2):
            record_id = row.get(id_field, "").strip()
            if not record_id:
                warnings.append(f"{filename}:{line_number}: missing {id_field}")
            elif record_id in seen:
                warnings.append(f"{filename}:{line_number}: duplicate {id_field} {record_id}")
            seen.add(record_id)

    for row in tables["costs.csv"]:
        if row.get("status", "").strip() == "confirmed_paid":
            if not document_ok(row, "evidence_document_id", active_documents) or not row.get("evidence_locator", "").strip():
                warnings.append(f"costs.csv:{row.get('cost_id', 'без ID')}: confirmed_paid without active document and locator")

    for row in tables["changes.csv"]:
        if row.get("status", "").strip() == "approved":
            decision_id = row.get("decision_id", "").strip()
            if not decision_id or decision_id not in decisions:
                warnings.append(f"changes.csv:{row.get('change_id', 'без ID')}: approved without registered owner decision")

    for row in tables["acceptance.csv"]:
        if row.get("status", "").strip() == "accepted" and not document_ok(row, "evidence_document_id", active_documents):
            warnings.append(f"acceptance.csv:{row.get('acceptance_id', 'без ID')}: accepted without active evidence document")

    for row in tables["commitments.csv"]:
        if row.get("status", "").strip() == "verified" and not document_ok(row, "closure_document_id", active_documents):
            warnings.append(f"commitments.csv:{row.get('commitment_id', 'без ID')}: verified without closure document")

    for row in tables["procurement.csv"]:
        if row.get("status", "").strip() == "accepted" and not document_ok(row, "evidence_document_id", active_documents):
            warnings.append(f"procurement.csv:{row.get('procurement_id', 'без ID')}: accepted without active evidence document")

    for line_number, record in jsonl_records["facts.jsonl"]:
        for field in ("statement_kind", "evidence_origin", "verification_status"):
            value = str(record.get(field, "")).strip()
            if not value:
                warnings.append(f"facts.jsonl:{line_number}: missing ontology field {field}")
            elif value not in DIMENSIONS[field]:
                warnings.append(f"facts.jsonl:{line_number}: unknown {field} {value}")
        source_id = str(record.get("source_document_id", "")).strip()
        if source_id and source_id not in active_documents:
            warnings.append(f"facts.jsonl:{line_number}: source document is not active: {source_id}")

    for line_number, record in jsonl_records["reading_runs.jsonl"]:
        source_id = str(record.get("source_document_id", "")).strip()
        if source_id not in active_documents:
            warnings.append(f"reading_runs.jsonl:{line_number}: source document is not active: {source_id or 'missing'}")
        if record.get("status") not in DIMENSIONS["reading_status"]:
            warnings.append(f"reading_runs.jsonl:{line_number}: unknown reading status")
        if record.get("status") == "complete":
            coverage = record.get("coverage", {})
            if not isinstance(coverage, dict):
                warnings.append(f"reading_runs.jsonl:{line_number}: coverage must be an object")
            summary_path = str(record.get("summary_path", "")).strip()
            if not complete_coverage_is_valid(coverage):
                warnings.append(f"reading_runs.jsonl:{line_number}: complete run has unresolved or inconsistent coverage")
            summary = root / summary_path if summary_path else None
            summaries_root = (root / ".home-control" / "summaries").resolve()
            try:
                summary_resolved = summary.resolve() if summary else None
                summary_inside = bool(
                    summary_resolved
                    and (summary_resolved == summaries_root or summaries_root in summary_resolved.parents)
                )
            except (OSError, RuntimeError):
                summary_resolved = None
                summary_inside = False
            if not summary_inside or not summary_resolved or not summary_resolved.is_file():
                warnings.append(f"reading_runs.jsonl:{line_number}: complete run has no existing summary file")
            version_key = (record.get("document_version"), str(record.get("sha256", "")).strip())
            if not version_key[1] or not version_key[0]:
                warnings.append(f"reading_runs.jsonl:{line_number}: complete run is not bound to an exact document version")
            elif version_key not in document_versions.get(source_id, set()):
                warnings.append(f"reading_runs.jsonl:{line_number}: document version and sha256 do not match the index")

    facts = jsonl_ids["facts.jsonl"]
    for line_number, record in jsonl_records["decisions.jsonl"]:
        if record.get("status") not in DIMENSIONS["owner_decision_status"]:
            warnings.append(f"decisions.jsonl:{line_number}: unknown owner decision status")
        source_fact_ids = id_list(
            record, "source_fact_ids", f"decisions.jsonl:{line_number}", warnings
        )
        if any(item not in facts for item in source_fact_ids):
            warnings.append(f"decisions.jsonl:{line_number}: unknown source fact link")

    for line_number, record in jsonl_records["comparables.jsonl"]:
        if record.get("status") not in DIMENSIONS["comparable_status"]:
            warnings.append(f"comparables.jsonl:{line_number}: unknown comparable status")
        source_document_id = str(record.get("source_document_id", "")).strip()
        source_url = str(record.get("source_url", "")).strip()
        if not source_document_id and not source_url:
            warnings.append(f"comparables.jsonl:{line_number}: no document or external source")
        elif source_document_id and source_document_id not in active_documents:
            warnings.append(f"comparables.jsonl:{line_number}: source document is not active")
        confirmation_fact = str(record.get("relevance_confirmation_fact_id", "")).strip()
        if record.get("status") == "confirmed_relevant" and confirmation_fact not in facts:
            warnings.append(f"comparables.jsonl:{line_number}: confirmed comparable has no confirmation fact")

    for line_number, record in jsonl_records["approved_requirements.jsonl"]:
        if record.get("baseline_status") not in DIMENSIONS["baseline_status"]:
            warnings.append(f"approved_requirements.jsonl:{line_number}: unknown baseline_status")
        source_fact_ids = id_list(
            record, "source_fact_ids", f"approved_requirements.jsonl:{line_number}", warnings
        )
        if not source_fact_ids or any(item not in facts for item in source_fact_ids):
            warnings.append(f"approved_requirements.jsonl:{line_number}: missing or unknown source_fact_ids")
        decision_id = str(record.get("decision_id", "")).strip()
        if decision_id and decision_id not in decisions:
            warnings.append(f"approved_requirements.jsonl:{line_number}: unknown decision_id {decision_id}")

    sites = jsonl_ids["sites.jsonl"]
    zones = jsonl_ids["zones.jsonl"]
    elements = jsonl_ids["physical_elements.jsonl"]
    systems = jsonl_ids["systems.jsonl"]
    assets = jsonl_ids["assets.jsonl"]
    routes = jsonl_ids["routes.jsonl"]
    events = jsonl_ids["asset_events.jsonl"]
    work_requests = jsonl_ids["work_requests.jsonl"]
    norm_references = jsonl_ids["norm_references.jsonl"]
    lifecycle_cost_assessments = jsonl_ids["lifecycle_cost_assessments.jsonl"]
    alternatives = jsonl_ids["alternatives.jsonl"]
    equipment_options = jsonl_ids["equipment_options.jsonl"]
    price_observations = jsonl_ids["price_observations.jsonl"]
    quote_items = jsonl_ids["quote_items.jsonl"]
    contractors = jsonl_ids["contractors.jsonl"]
    suppliers = jsonl_ids["suppliers.jsonl"]
    zone_records = records_by_id(jsonl_records["zones.jsonl"], "zone_id")
    element_records = records_by_id(jsonl_records["physical_elements.jsonl"], "element_id")
    system_records = records_by_id(jsonl_records["systems.jsonl"], "system_id")

    for line_number, record in jsonl_records["sites.jsonl"]:
        location = f"sites.jsonl:{line_number}"
        if str(record.get("project_id", "")).strip() != project_id:
            warnings.append(f"{location}: site is not linked to the current project_id")
        if record.get("site_kind") not in DIMENSIONS["site_kind"]:
            warnings.append(f"{location}: unknown site_kind")
        if record.get("status") not in DIMENSIONS["site_status"]:
            warnings.append(f"{location}: unknown site status")
        source_fact_ids = id_list(record, "source_fact_ids", location, warnings)
        if any(item not in facts for item in source_fact_ids):
            warnings.append(f"{location}: unknown source fact link")

    for line_number, record in jsonl_records["zones.jsonl"]:
        location = f"zones.jsonl:{line_number}"
        site_id = str(record.get("site_id", "")).strip()
        if site_id not in sites:
            warnings.append(f"zones.jsonl:{line_number}: unknown site_id")
        parent_zone_id = str(record.get("parent_zone_id", "")).strip()
        if parent_zone_id and parent_zone_id not in zones:
            warnings.append(f"zones.jsonl:{line_number}: unknown parent_zone_id {parent_zone_id}")
        elif parent_zone_id:
            parent_site_id = str(zone_records[parent_zone_id].get("site_id", "")).strip()
            if parent_site_id != site_id:
                warnings.append(f"{location}: parent zone belongs to a different site")
        if record.get("zone_kind") not in DIMENSIONS["zone_kind"]:
            warnings.append(f"zones.jsonl:{line_number}: unknown zone_kind")
        source_fact_ids = id_list(record, "source_fact_ids", location, warnings)
        if any(item not in facts for item in source_fact_ids):
            warnings.append(f"{location}: unknown source fact link")

    reported_cycles: set[tuple[str, ...]] = set()
    for start_zone_id in zones:
        chain: list[str] = []
        positions: dict[str, int] = {}
        current = start_zone_id
        while current in zone_records:
            if current in positions:
                cycle = chain[positions[current]:]
                canonical = tuple(sorted(cycle))
                if canonical not in reported_cycles:
                    reported_cycles.add(canonical)
                    warnings.append(f"zones.jsonl: parent cycle detected: {' -> '.join(cycle + [current])}")
                break
            positions[current] = len(chain)
            chain.append(current)
            current = str(zone_records[current].get("parent_zone_id", "")).strip()
            if not current:
                break

    for line_number, record in jsonl_records["physical_elements.jsonl"]:
        location = f"physical_elements.jsonl:{line_number}"
        zone_id = str(record.get("zone_id", "")).strip()
        if zone_id not in zones:
            warnings.append(f"{location}: missing or unknown zone_id {zone_id or 'missing'}")
        source_fact_ids = id_list(record, "source_fact_ids", location, warnings)
        if any(item not in facts for item in source_fact_ids):
            warnings.append(f"{location}: unknown source fact link")

    for line_number, record in jsonl_records["systems.jsonl"]:
        location = f"systems.jsonl:{line_number}"
        linked_sites = id_list(record, "site_ids", location, warnings)
        if not linked_sites or any(item not in sites for item in linked_sites):
            warnings.append(f"{location}: missing or unknown site_ids")
        if record.get("operational_status") not in DIMENSIONS["system_operational_status"]:
            warnings.append(f"{location}: unknown operational_status")
        source_fact_ids = id_list(record, "source_fact_ids", location, warnings)
        if any(item not in facts for item in source_fact_ids):
            warnings.append(f"{location}: unknown source fact link")

    for line_number, record in jsonl_records["assets.jsonl"]:
        site_id = str(record.get("site_id", "")).strip()
        if site_id not in sites:
            warnings.append(f"assets.jsonl:{line_number}: unknown site_id")
        zone_id = str(record.get("zone_id", "")).strip()
        if zone_id and zone_id not in zones:
            warnings.append(f"assets.jsonl:{line_number}: unknown zone_id {zone_id}")
        elif zone_id and str(zone_records[zone_id].get("site_id", "")).strip() != site_id:
            warnings.append(f"assets.jsonl:{line_number}: asset site_id conflicts with zone site_id")
        system_ids = id_list(record, "system_ids", f"assets.jsonl:{line_number}", warnings)
        if any(item not in systems for item in system_ids):
            warnings.append(f"assets.jsonl:{line_number}: unknown system link")
        else:
            for system_id in system_ids:
                system_sites = id_list(
                    system_records[system_id], "site_ids", f"systems.jsonl:{system_id}", warnings
                )
                if site_id and site_id not in system_sites:
                    warnings.append(f"assets.jsonl:{line_number}: asset site_id is not linked to its system")
        mounting_element_id = str(record.get("mounting_element_id", "")).strip()
        if mounting_element_id and mounting_element_id not in elements:
            warnings.append(f"assets.jsonl:{line_number}: unknown mounting_element_id")
        elif mounting_element_id:
            element_zone_id = str(element_records[mounting_element_id].get("zone_id", "")).strip()
            element_site_id = (
                str(zone_records[element_zone_id].get("site_id", "")).strip()
                if element_zone_id in zone_records
                else ""
            )
            if element_site_id and element_site_id != site_id:
                warnings.append(f"assets.jsonl:{line_number}: mounting element belongs to a different site")
        if record.get("lifecycle_status") not in DIMENSIONS["asset_lifecycle_status"]:
            warnings.append(f"assets.jsonl:{line_number}: unknown lifecycle_status")
        if record.get("operational_status") not in DIMENSIONS["asset_operational_status"]:
            warnings.append(f"assets.jsonl:{line_number}: unknown operational_status")
        source_fact_ids = id_list(record, "source_fact_ids", f"assets.jsonl:{line_number}", warnings)
        if any(item not in facts for item in source_fact_ids):
            warnings.append(f"assets.jsonl:{line_number}: unknown source fact link")

    physical_targets = sites | zones | elements | routes | systems | assets
    interface_targets = systems | assets
    for line_number, record in jsonl_records["asset_interfaces.jsonl"]:
        endpoints = id_list(
            record, "endpoint_entity_ids", f"asset_interfaces.jsonl:{line_number}", warnings
        )
        if len(set(endpoints)) < 2 or any(item not in interface_targets for item in endpoints):
            warnings.append(f"asset_interfaces.jsonl:{line_number}: at least two known system or asset endpoints are required")
        if record.get("interface_type") not in DIMENSIONS["interface_type"]:
            warnings.append(f"asset_interfaces.jsonl:{line_number}: unknown interface_type")
        source_fact_ids = id_list(
            record, "source_fact_ids", f"asset_interfaces.jsonl:{line_number}", warnings
        )
        if any(item not in facts for item in source_fact_ids):
            warnings.append(f"asset_interfaces.jsonl:{line_number}: unknown source fact link")

    for line_number, record in jsonl_records["routes.jsonl"]:
        site_id = str(record.get("site_id", "")).strip()
        if site_id not in sites:
            warnings.append(f"routes.jsonl:{line_number}: unknown site_id")
        zone_ids = id_list(record, "zone_ids", f"routes.jsonl:{line_number}", warnings)
        system_ids = id_list(record, "system_ids", f"routes.jsonl:{line_number}", warnings)
        if any(item not in zones for item in zone_ids):
            warnings.append(f"routes.jsonl:{line_number}: unknown zone link")
        elif any(str(zone_records[item].get("site_id", "")).strip() != site_id for item in zone_ids):
            warnings.append(f"routes.jsonl:{line_number}: route zone belongs to a different site")
        if any(item not in systems for item in system_ids):
            warnings.append(f"routes.jsonl:{line_number}: unknown system link")
        else:
            for system_id in system_ids:
                if site_id not in id_list(
                    system_records[system_id], "site_ids", f"systems.jsonl:{system_id}", warnings
                ):
                    warnings.append(f"routes.jsonl:{line_number}: route system is not linked to route site")
        source_fact_ids = id_list(record, "source_fact_ids", f"routes.jsonl:{line_number}", warnings)
        if any(item not in facts for item in source_fact_ids):
            warnings.append(f"routes.jsonl:{line_number}: unknown source fact link")

    for line_number, record in jsonl_records["asset_events.jsonl"]:
        event_asset_ids = id_list(
            record, "asset_ids", f"asset_events.jsonl:{line_number}", warnings
        )
        if not event_asset_ids or any(item not in assets for item in event_asset_ids):
            warnings.append(f"asset_events.jsonl:{line_number}: missing or unknown asset_ids")
        if record.get("event_type") not in DIMENSIONS["asset_event_type"]:
            warnings.append(f"asset_events.jsonl:{line_number}: unknown event_type")
        work_request_id = str(record.get("work_request_id", "")).strip()
        if work_request_id and work_request_id not in work_requests:
            warnings.append(f"asset_events.jsonl:{line_number}: unknown work_request_id")
        source_fact_ids = id_list(
            record, "source_fact_ids", f"asset_events.jsonl:{line_number}", warnings
        )
        if any(item not in facts for item in source_fact_ids):
            warnings.append(f"asset_events.jsonl:{line_number}: unknown source fact link")
        source_id = str(record.get("source_document_id", "")).strip()
        if source_id and source_id not in active_documents:
            warnings.append(f"asset_events.jsonl:{line_number}: source document is not active")
        contractor_id = str(record.get("provider_contractor_id", "")).strip()
        supplier_id = str(record.get("provider_supplier_id", "")).strip()
        if contractor_id and contractor_id not in contractors:
            warnings.append(f"asset_events.jsonl:{line_number}: unknown provider_contractor_id")
        if supplier_id and supplier_id not in suppliers:
            warnings.append(f"asset_events.jsonl:{line_number}: unknown provider_supplier_id")
        removed_asset_ids = id_list(
            record, "removed_asset_ids", f"asset_events.jsonl:{line_number}", warnings
        )
        installed_asset_ids = id_list(
            record, "installed_asset_ids", f"asset_events.jsonl:{line_number}", warnings
        )
        if any(item not in assets for item in removed_asset_ids):
            warnings.append(f"asset_events.jsonl:{line_number}: unknown removed asset link")
        if any(item not in assets for item in installed_asset_ids):
            warnings.append(f"asset_events.jsonl:{line_number}: unknown installed asset link")
        if record.get("event_type") == "replaced":
            if not removed_asset_ids or not installed_asset_ids:
                warnings.append(
                    f"asset_events.jsonl:{line_number}: replacement event requires removed_asset_ids and installed_asset_ids"
                )

    for line_number, record in jsonl_records["maintenance_plans.jsonl"]:
        location = f"maintenance_plans.jsonl:{line_number}"
        target_ids = id_list(record, "target_entity_ids", location, warnings)
        maintenance_targets = assets | systems | elements | routes
        if not target_ids or any(item not in maintenance_targets for item in target_ids):
            warnings.append(f"{location}: missing or unknown target_entity_ids")
        basis_fact_ids = id_list(record, "basis_fact_ids", location, warnings)
        norm_reference_ids = id_list(record, "norm_reference_ids", location, warnings)
        if any(item not in facts for item in basis_fact_ids):
            warnings.append(f"{location}: unknown basis fact link")
        if any(item not in norm_references for item in norm_reference_ids):
            warnings.append(f"{location}: unknown norm reference link")
        last_event_id = str(record.get("last_completed_event_id", "")).strip()
        if last_event_id and last_event_id not in events:
            warnings.append(f"{location}: unknown last_completed_event_id")
        if record.get("status") not in DIMENSIONS["maintenance_plan_status"]:
            warnings.append(f"{location}: unknown maintenance plan status")
        if record.get("trigger_type") not in DIMENSIONS["maintenance_trigger_type"]:
            warnings.append(f"{location}: unknown maintenance trigger type")

    for line_number, record in jsonl_records["condition_assessments.jsonl"]:
        if str(record.get("target_entity_id", "")).strip() not in (assets | systems | elements | routes):
            warnings.append(f"condition_assessments.jsonl:{line_number}: unknown target_entity_id")
        if record.get("condition_status") not in DIMENSIONS["condition_status"]:
            warnings.append(f"condition_assessments.jsonl:{line_number}: unknown condition_status")
        source_fact_ids = id_list(
            record, "source_fact_ids", f"condition_assessments.jsonl:{line_number}", warnings
        )
        if any(item not in facts for item in source_fact_ids):
            warnings.append(f"condition_assessments.jsonl:{line_number}: unknown source fact link")
        assessor_id = str(record.get("assessor_contractor_id", "")).strip()
        if assessor_id and assessor_id not in contractors:
            warnings.append(f"condition_assessments.jsonl:{line_number}: unknown assessor_contractor_id")

    for line_number, record in jsonl_records["work_requests.jsonl"]:
        location = f"work_requests.jsonl:{line_number}"
        target_ids = id_list(
            record, "target_entity_ids", location, warnings
        )
        if not target_ids:
            warnings.append(f"{location}: no target entities")
        elif any(item not in physical_targets for item in target_ids):
            warnings.append(f"{location}: unknown target entity")
        if record.get("request_type") not in DIMENSIONS["work_request_type"]:
            warnings.append(f"{location}: unknown request_type")
        if record.get("status") not in DIMENSIONS["work_request_status"]:
            warnings.append(f"{location}: unknown work request status")
        source_fact_ids = id_list(record, "source_fact_ids", location, warnings)
        if any(item not in facts for item in source_fact_ids):
            warnings.append(f"{location}: unknown source fact link")
        owner_decision_id = str(record.get("owner_decision_id", "")).strip()
        if record.get("status") in {"approved", "planned", "in_progress", "completed"}:
            if owner_decision_id not in decisions:
                warnings.append(f"{location}: approved-or-later request has no owner decision")

    for line_number, record in jsonl_records["lifecycle_cost_assessments.jsonl"]:
        location = f"lifecycle_cost_assessments.jsonl:{line_number}"
        work_request_id = str(record.get("work_request_id", "")).strip()
        if work_request_id not in work_requests:
            warnings.append(f"{location}: unknown work_request_id")
        target_asset_ids = id_list(
            record, "target_asset_ids", location, warnings
        )
        if any(item not in assets for item in target_asset_ids):
            warnings.append(f"{location}: unknown target asset")
        alternative_ids = id_list(record, "alternative_ids", location, warnings)
        option_ids = id_list(record, "equipment_option_ids", location, warnings)
        observation_ids = id_list(record, "price_observation_ids", location, warnings)
        source_fact_ids = id_list(record, "source_fact_ids", location, warnings)
        if any(item not in alternatives for item in alternative_ids):
            warnings.append(f"{location}: unknown alternative link")
        if any(item not in equipment_options for item in option_ids):
            warnings.append(f"{location}: unknown equipment option link")
        if any(item not in price_observations for item in observation_ids):
            warnings.append(f"{location}: unknown price observation link")
        if any(item not in facts for item in source_fact_ids):
            warnings.append(f"{location}: unknown source fact link")
        if record.get("status") not in DIMENSIONS["lifecycle_cost_status"]:
            warnings.append(f"{location}: unknown lifecycle cost status")
        components = record.get("components", [])
        if not isinstance(components, list):
            warnings.append(f"{location}: components must be an array")
        else:
            for component_number, component in enumerate(components, 1):
                if not isinstance(component, dict) or component.get("component_type") not in DIMENSIONS["lifecycle_cost_component"]:
                    warnings.append(f"{location}: component {component_number} has unknown component_type")
        if not record.get("scenario_ids") and not record.get("scenarios"):
            warnings.append(f"{location}: no lifecycle scenarios")

    for line_number, record in jsonl_records["lifecycle_decisions.jsonl"]:
        location = f"lifecycle_decisions.jsonl:{line_number}"
        if str(record.get("work_request_id", "")).strip() not in work_requests:
            warnings.append(f"lifecycle_decisions.jsonl:{line_number}: unknown work_request_id")
        target_asset_ids = id_list(
            record, "target_asset_ids", f"lifecycle_decisions.jsonl:{line_number}", warnings
        )
        alternative_ids = id_list(
            record, "alternative_ids", f"lifecycle_decisions.jsonl:{line_number}", warnings
        )
        if any(item not in assets for item in target_asset_ids):
            warnings.append(f"lifecycle_decisions.jsonl:{line_number}: unknown target asset")
        if record.get("action") not in DIMENSIONS["lifecycle_action"]:
            warnings.append(f"lifecycle_decisions.jsonl:{line_number}: unknown lifecycle action")
        if record.get("status") not in DIMENSIONS["lifecycle_decision_status"]:
            warnings.append(f"{location}: unknown lifecycle decision status")
        if any(item not in alternatives for item in alternative_ids):
            warnings.append(f"lifecycle_decisions.jsonl:{line_number}: unknown alternative link")
        assessment_ids = id_list(record, "lifecycle_cost_assessment_ids", location, warnings)
        if any(item not in lifecycle_cost_assessments for item in assessment_ids):
            warnings.append(f"{location}: unknown lifecycle cost assessment link")
        owner_decision_id = str(record.get("owner_decision_id", "")).strip()
        if owner_decision_id and owner_decision_id not in decisions:
            warnings.append(f"lifecycle_decisions.jsonl:{line_number}: unknown owner_decision_id")
        if record.get("status") in {"owner_decided", "implemented"} and owner_decision_id not in decisions:
            warnings.append(f"{location}: owner-decided lifecycle action has no owner decision")
        implemented_event_ids = id_list(record, "implemented_event_ids", location, warnings)
        if any(item not in events for item in implemented_event_ids):
            warnings.append(f"{location}: unknown implemented event link")
        if record.get("status") == "implemented" and not implemented_event_ids:
            warnings.append(f"{location}: implemented decision has no implemented_event_ids")

    for line_number, record in jsonl_records["quotes.jsonl"]:
        if str(record.get("source_document_id", "")).strip() not in active_documents:
            warnings.append(f"quotes.jsonl:{line_number}: quote has no active source document")
        contractor_id = str(record.get("contractor_id", "")).strip()
        supplier_id = str(record.get("supplier_id", "")).strip()
        if bool(contractor_id) == bool(supplier_id):
            warnings.append(f"quotes.jsonl:{line_number}: quote must have exactly one contractor_id or supplier_id")
        if contractor_id and contractor_id not in contractors:
            warnings.append(f"quotes.jsonl:{line_number}: unknown contractor_id")
        if supplier_id and supplier_id not in suppliers:
            warnings.append(f"quotes.jsonl:{line_number}: unknown supplier_id")

    quotes = jsonl_ids["quotes.jsonl"]
    requirements = jsonl_ids["approved_requirements.jsonl"]
    for line_number, record in jsonl_records["quote_items.jsonl"]:
        if str(record.get("quote_id", "")).strip() not in quotes:
            warnings.append(f"quote_items.jsonl:{line_number}: unknown quote_id")
        linked = id_list(
            record, "approved_requirement_ids", f"quote_items.jsonl:{line_number}", warnings
        )
        if any(item not in requirements for item in linked):
            warnings.append(f"quote_items.jsonl:{line_number}: unknown approved requirement link")
        target_ids = id_list(record, "target_entity_ids", f"quote_items.jsonl:{line_number}", warnings)
        quote_item_targets = work_requests | assets | systems | sites | zones
        if any(item not in quote_item_targets for item in target_ids):
            warnings.append(f"quote_items.jsonl:{line_number}: unknown target entity link")
        if not str(record.get("raw_text", "")).strip() or not str(record.get("locator", "")).strip():
            warnings.append(f"quote_items.jsonl:{line_number}: raw_text and locator are required")
        if record.get("proposal_match_status") not in DIMENSIONS["proposal_match_status"]:
            warnings.append(f"quote_items.jsonl:{line_number}: unknown proposal_match_status")
        if record.get("verifiability") not in DIMENSIONS["verifiability"]:
            warnings.append(f"quote_items.jsonl:{line_number}: unknown verifiability")
        quantity = record.get("quantity")
        unit_price = record.get("unit_price")
        amount = record.get("amount")
        if all(isinstance(value, (int, float)) for value in (quantity, unit_price, amount)):
            calculated = quantity * unit_price
            if abs(calculated - amount) > max(0.01, abs(amount) * 0.000001):
                warnings.append(f"quote_items.jsonl:{line_number}: quantity × unit_price differs from amount")

    for line_number, record in jsonl_records["equipment_options.jsonl"]:
        location = f"equipment_options.jsonl:{line_number}"
        work_request_id = str(record.get("work_request_id", "")).strip()
        if work_request_id and work_request_id not in work_requests:
            warnings.append(f"{location}: unknown work_request_id")
        replaced = id_list(
            record, "replaces_asset_ids", location, warnings
        )
        if any(item not in assets for item in replaced):
            warnings.append(f"{location}: unknown replaced asset link")
        source_fact_ids = id_list(record, "source_fact_ids", location, warnings)
        if any(item not in facts for item in source_fact_ids):
            warnings.append(f"{location}: unknown source fact link")
        if record.get("status") not in DIMENSIONS["equipment_option_status"]:
            warnings.append(f"{location}: unknown equipment option status")
        owner_decision_id = str(record.get("owner_decision_id", "")).strip()
        if owner_decision_id and owner_decision_id not in decisions:
            warnings.append(f"{location}: unknown owner_decision_id")
        if record.get("status") == "selected" and owner_decision_id not in decisions:
            warnings.append(f"{location}: selected option has no owner decision")

    for line_number, record in jsonl_records["price_observations.jsonl"]:
        location = f"price_observations.jsonl:{line_number}"
        supplier_id = str(record.get("seller_supplier_id", "")).strip()
        contractor_id = str(record.get("seller_contractor_id", "")).strip()
        if supplier_id and contractor_id:
            warnings.append(f"{location}: seller must not be both a supplier and a contractor")
        if supplier_id and supplier_id not in suppliers:
            warnings.append(f"{location}: unknown seller_supplier_id")
        if contractor_id and contractor_id not in contractors:
            warnings.append(f"{location}: unknown seller_contractor_id")
        subject_id = str(record.get("subject_entity_id", "")).strip()
        price_subjects = assets | equipment_options | work_requests | quote_items
        if subject_id and subject_id not in price_subjects:
            warnings.append(f"{location}: unknown subject_entity_id")
        source_fact_ids = id_list(record, "source_fact_ids", location, warnings)
        if any(item not in facts for item in source_fact_ids):
            warnings.append(f"{location}: unknown source fact link")

    all_registered_ids = {project_id, *document_versions}
    for identifiers in jsonl_ids.values():
        all_registered_ids.update(identifiers)
    for line_number, record in jsonl_records["findings.jsonl"]:
        source_ids = id_list(record, "source_ids", f"findings.jsonl:{line_number}", warnings)
        if not source_ids:
            warnings.append(f"findings.jsonl:{line_number}: no registered sources")
        elif any(item not in all_registered_ids for item in source_ids):
            warnings.append(f"findings.jsonl:{line_number}: unknown source link")

    for line_number, record in jsonl_records["alternatives.jsonl"]:
        location = f"alternatives.jsonl:{line_number}"
        if not record.get("checked_at") or not record.get("source_urls"):
            warnings.append(f"{location}: missing search date or source URLs")
        linked = id_list(
            record, "baseline_requirement_ids", location, warnings
        )
        if any(item not in requirements for item in linked):
            warnings.append(f"{location}: unknown baseline requirement link")
        work_request_id = str(record.get("work_request_id", "")).strip()
        if work_request_id and work_request_id not in work_requests:
            warnings.append(f"{location}: unknown work_request_id")

    return warnings


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("project_dir", type=Path)
    parser.add_argument("--write-report", action="store_true")
    args = parser.parse_args()
    root = validate_project(args.project_dir)
    warnings = audit(root)
    lines = ["# Проверка данных проекта", "", f"Найдено предупреждений: {len(warnings)}", ""]
    lines.extend(f"- {warning}" for warning in warnings)
    if not warnings:
        lines.append("- Формальных разрывов связей не найдено.")
    output = "\n".join(lines) + "\n"
    if args.write_report:
        report = root / ".home-control" / "reports" / "data-audit.md"
        report.parent.mkdir(parents=True, exist_ok=True)
        temporary = report.with_name(f".{report.name}.{uuid.uuid4().hex}.tmp")
        try:
            temporary.write_text(output, encoding="utf-8")
            temporary.replace(report)
        finally:
            if temporary.exists():
                temporary.unlink()
        print(report)
    else:
        print(output, end="")
    return 1 if warnings else 0


if __name__ == "__main__":
    raise SystemExit(main())
