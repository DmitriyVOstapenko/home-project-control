#!/usr/bin/env python3
"""Audit local project registries without changing source documents."""

from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path

from inspect_project import is_linklike, require_ready_project
from management_model import REGISTRIES as MANAGEMENT_REGISTRIES, validate_and_enrich
from render_report_pdf import write_report_pair


PLUGIN_ROOT = Path(__file__).resolve().parents[3]
ONTOLOGY = json.loads((PLUGIN_ROOT / "schemas" / "ontology.json").read_text(encoding="utf-8"))
DIMENSIONS = {name: set(values) for name, values in ONTOLOGY["dimensions"].items()}
STRUCTURE = json.loads((PLUGIN_ROOT / "schemas" / "project-structure.json").read_text(encoding="utf-8"))
PROPOSAL_CONTRACT = json.loads(
    (PLUGIN_ROOT / "schemas" / "proposal-review-contract.json").read_text(encoding="utf-8")
)


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


def validate_context_layer(
    root: Path,
    jsonl_records: dict[str, list[tuple[int, dict]]],
    jsonl_ids: dict[str, set[str]],
    active_documents: set[str],
    document_versions: dict[str, set[tuple[object, str]]],
    document_paths: dict[str, str],
    complete_read_versions: set[tuple[str, object, str]],
    warnings: list[str],
) -> None:
    facts_by_id = records_by_id(jsonl_records["facts.jsonl"], "fact_id")
    decisions_by_id = records_by_id(jsonl_records["decisions.jsonl"], "decision_id")
    as_is_by_id = records_by_id(jsonl_records["as_is_snapshots.jsonl"], "as_is_snapshot_id")
    baselines = jsonl_ids["baseline_snapshots.jsonl"]
    packages = jsonl_ids["project_packages.jsonl"]
    gaps = jsonl_ids["information_gaps.jsonl"]
    all_registered_ids: set[str] = set(document_versions)
    for identifiers in jsonl_ids.values():
        all_registered_ids.update(identifiers)
    complete_extraction_versions: set[tuple[str, object, str]] = set()
    for _, extraction in jsonl_records["fact_extraction_runs.jsonl"]:
        key = (
            str(extraction.get("source_document_id", "")).strip(),
            extraction.get("document_version"),
            str(extraction.get("sha256", "")).strip(),
        )
        expected = normalized_string_set(extraction.get("expected_sections"))
        checked = normalized_string_set(extraction.get("checked_sections"))
        coverage_gaps = extraction.get("coverage_gaps")
        extracted_fact_ids = extraction.get("fact_ids")
        if (
            extraction.get("status") == "complete"
            and key in complete_read_versions
            and expected
            and checked == expected
            and isinstance(coverage_gaps, list)
            and not coverage_gaps
            and isinstance(extracted_fact_ids, list)
            and bool(extracted_fact_ids)
        ):
            complete_extraction_versions.add(key)

    for line_number, batch in jsonl_records["document_intake_batches.jsonl"]:
        location = f"document_intake_batches.jsonl:{line_number}"
        if batch.get("status") != "applied":
            warnings.append(f"{location}: status must be applied")
        if not isinstance(batch.get("applied_at_utc"), str) or not batch["applied_at_utc"].strip():
            warnings.append(f"{location}: applied_at_utc is required")
        if not isinstance(batch.get("batch_description", ""), str):
            warnings.append(f"{location}: batch_description must be a string")
        items = batch.get("items")
        if not isinstance(items, list) or not items:
            warnings.append(f"{location}: items must be a non-empty array")
            continue
        for number, item in enumerate(items, 1):
            item_location = f"{location}:item {number}"
            if not isinstance(item, dict):
                warnings.append(f"{item_location}: expected an object")
                continue
            document_id = str(item.get("document_id", "")).strip()
            sha256 = str(item.get("source_sha256", "")).strip()
            if document_id not in document_versions:
                warnings.append(f"{item_location}: unknown document_id")
            elif not any(value[1] == sha256 for value in document_versions[document_id]):
                warnings.append(f"{item_location}: source_sha256 is not a registered document version")
            if document_id in document_paths and item.get("relative_path") != document_paths[document_id]:
                warnings.append(f"{item_location}: relative_path does not match the document index")
            for field in ("source_filename", "relative_path", "description"):
                if not isinstance(item.get(field), str) or not item[field].strip():
                    warnings.append(f"{item_location}: missing required field {field}")
            if item.get("action") not in {"copy", "already_in_place", "use_existing_identical"}:
                warnings.append(f"{item_location}: unknown intake action")
            if item.get("verification_status") != "unreviewed":
                warnings.append(f"{item_location}: declared intake context must remain unreviewed")

    seen_snapshot_versions: dict[int, str] = {}
    superseded_snapshot_ids: set[str] = set()
    entity_fields = {
        "site_ids": jsonl_ids["sites.jsonl"],
        "zone_ids": jsonl_ids["zones.jsonl"],
        "physical_element_ids": jsonl_ids["physical_elements.jsonl"],
        "system_ids": jsonl_ids["systems.jsonl"],
        "asset_ids": jsonl_ids["assets.jsonl"],
        "route_ids": jsonl_ids["routes.jsonl"],
        "asset_event_ids": jsonl_ids["asset_events.jsonl"],
        "condition_assessment_ids": jsonl_ids["condition_assessments.jsonl"],
    }
    for line_number, snapshot in jsonl_records["as_is_snapshots.jsonl"]:
        location = f"as_is_snapshots.jsonl:{line_number}"
        snapshot_id = str(snapshot.get("as_is_snapshot_id", "")).strip()
        version = snapshot.get("snapshot_version")
        if not isinstance(version, int) or isinstance(version, bool) or version < 1:
            warnings.append(f"{location}: snapshot_version must be a positive integer")
        elif version in seen_snapshot_versions:
            warnings.append(f"{location}: duplicate snapshot_version {version}")
        else:
            seen_snapshot_versions[version] = snapshot_id
        for field in ("scope", "captured_at"):
            if not isinstance(snapshot.get(field), str) or not snapshot[field].strip():
                warnings.append(f"{location}: missing required field {field}")
        decision_id = str(snapshot.get("owner_decision_id", "")).strip()
        decision = decisions_by_id.get(decision_id)
        if (
            decision is None
            or decision.get("decision_type") != "as_is_snapshot_acceptance"
            or decision.get("status") != "approved"
            or decision.get("approved_by") != "owner"
        ):
            warnings.append(f"{location}: no explicit approved owner as-is decision")
        supersedes = str(snapshot.get("supersedes_as_is_snapshot_id", "")).strip()
        if version == 1 and supersedes:
            warnings.append(f"{location}: snapshot version 1 must not supersede another snapshot")
        if isinstance(version, int) and version > 1:
            prior = as_is_by_id.get(supersedes)
            if prior is None or prior.get("snapshot_version") != version - 1:
                warnings.append(f"{location}: versioned as-is snapshot must supersede the immediately preceding snapshot")
            else:
                superseded_snapshot_ids.add(supersedes)
        source_fact_ids = id_list(snapshot, "source_fact_ids", location, warnings)
        if not source_fact_ids or any(value not in facts_by_id for value in source_fact_ids):
            warnings.append(f"{location}: missing or unknown source_fact_ids")
        for fact_id in source_fact_ids:
            fact = facts_by_id.get(fact_id)
            if fact is not None and fact.get("verification_status") in {
                "unreviewed", "extracted", "rejected", "superseded"
            }:
                warnings.append(f"{location}: source fact {fact_id} is not ready for an as-is snapshot")
        superseded_inside_snapshot = {
            prior_id
            for fact_id in source_fact_ids
            for prior_id in (
                normalized_string_set(facts_by_id.get(fact_id, {}).get("supersedes_fact_ids", [])) or set()
            )
            if prior_id in source_fact_ids
        }
        if superseded_inside_snapshot:
            warnings.append(f"{location}: snapshot contains both a fact update and the fact it supersedes")
        for field, known in entity_fields.items():
            if not isinstance(snapshot.get(field), list):
                warnings.append(f"{location}: {field} must be an array")
            values = id_list(snapshot, field, location, warnings)
            if any(value not in known for value in values):
                warnings.append(f"{location}: {field} contains an unknown link")
        if not isinstance(snapshot.get("information_gap_ids"), list):
            warnings.append(f"{location}: information_gap_ids must be an array")
        gap_ids = id_list(snapshot, "information_gap_ids", location, warnings)
        if any(value not in gaps for value in gap_ids):
            warnings.append(f"{location}: information_gap_ids contains an unknown link")
        limitations = snapshot.get("limitations")
        if not isinstance(limitations, list) or any(
            not isinstance(value, str) or not value.strip() for value in limitations
        ):
            warnings.append(f"{location}: limitations must be a string array")
            limitations = []
        uncertain_fact_ids = [
            fact_id
            for fact_id in source_fact_ids
            if facts_by_id.get(fact_id, {}).get("verification_status")
            in {"conflicted", "requires_confirmation"}
        ]
        if uncertain_fact_ids and not gap_ids and not limitations:
            warnings.append(f"{location}: uncertain source facts require a gap or explicit limitation")
        versions = snapshot.get("document_versions")
        snapshot_document_keys: set[tuple[str, object, str]] = set()
        if not isinstance(versions, list):
            warnings.append(f"{location}: document_versions must be an array")
        else:
            for number, item in enumerate(versions, 1):
                if not isinstance(item, dict):
                    warnings.append(f"{location}: document version {number} must be an object")
                    continue
                document_id = str(item.get("document_id", "")).strip()
                version_key = (item.get("document_version"), str(item.get("sha256", "")).strip())
                if document_id not in active_documents or version_key not in document_versions.get(document_id, set()):
                    warnings.append(f"{location}: document version {number} is not an exact active version")
                exact_key = (document_id, version_key[0], version_key[1])
                snapshot_document_keys.add(exact_key)
                if exact_key not in complete_read_versions or exact_key not in complete_extraction_versions:
                    warnings.append(f"{location}: document version {number} has no complete reading and fact extraction")
        for fact_id in source_fact_ids:
            fact = facts_by_id.get(fact_id)
            if fact is None:
                continue
            source_document_id = str(fact.get("source_document_id", "")).strip()
            if not source_document_id:
                continue
            fact_key = (
                source_document_id,
                fact.get("document_version"),
                str(fact.get("sha256", "")).strip(),
            )
            if fact_key not in snapshot_document_keys:
                warnings.append(f"{location}: source fact {fact_id} document version is absent from the snapshot")
    if as_is_by_id and len(set(as_is_by_id) - superseded_snapshot_ids) != 1:
        warnings.append("as_is_snapshots.jsonl: more than one current as-is snapshot")

    requests_by_series: dict[str, list[dict]] = {}
    for line_number, request in jsonl_records["analysis_requests.jsonl"]:
        location = f"analysis_requests.jsonl:{line_number}"
        series_id = str(request.get("request_series_id", "")).strip()
        if not series_id:
            warnings.append(f"{location}: request_series_id is required")
        else:
            requests_by_series.setdefault(series_id, []).append(request)
        version = request.get("request_version")
        if not isinstance(version, int) or isinstance(version, bool) or version < 1:
            warnings.append(f"{location}: request_version must be a positive integer")
        for field in ("request_text", "requested_at"):
            if not isinstance(request.get(field), str) or not request[field].strip():
                warnings.append(f"{location}: missing required field {field}")
        if request.get("request_type") not in DIMENSIONS["analysis_request_type"]:
            warnings.append(f"{location}: unknown analysis request type")
        if request.get("status") not in DIMENSIONS["analysis_request_status"]:
            warnings.append(f"{location}: unknown analysis request status")
        context_mode = request.get("context_mode")
        if context_mode not in DIMENSIONS["analysis_context_mode"]:
            warnings.append(f"{location}: unknown analysis context mode")
        as_is_id = str(request.get("as_is_snapshot_id", "")).strip()
        baseline_id = str(request.get("baseline_snapshot_id", "")).strip()
        if as_is_id and as_is_id not in as_is_by_id:
            warnings.append(f"{location}: unknown as_is_snapshot_id")
        if baseline_id and baseline_id not in baselines:
            warnings.append(f"{location}: unknown baseline_snapshot_id")
        if context_mode in {"as_is_and_baseline", "as_is_only"} and not as_is_id:
            warnings.append(f"{location}: context mode requires an as-is snapshot")
        if context_mode in {"as_is_and_baseline", "baseline_only"} and not baseline_id:
            warnings.append(f"{location}: context mode requires a baseline snapshot")
        linked_fields = {
            "source_document_ids": set(document_versions),
            "package_ids": packages,
            "information_gap_ids": gaps,
            "target_entity_ids": all_registered_ids,
        }
        for field, known in linked_fields.items():
            if not isinstance(request.get(field), list):
                warnings.append(f"{location}: {field} must be an array")
            values = id_list(request, field, location, warnings)
            if any(value not in known for value in values):
                warnings.append(f"{location}: {field} contains an unknown link")
        outputs = normalized_string_set(request.get("requested_outputs"))
        if not outputs:
            warnings.append(f"{location}: requested_outputs must be a non-empty unique string array")
        result_paths = request.get("result_paths")
        if not isinstance(result_paths, list) or any(
            not isinstance(value, str) or not value.strip() for value in result_paths
        ):
            warnings.append(f"{location}: result_paths must be a string array")
            result_paths = []
        if request.get("status") == "completed":
            if context_mode == "unbound":
                warnings.append(f"{location}: completed request cannot have unbound context")
            if not result_paths:
                warnings.append(f"{location}: completed request must identify a saved result")
            for value in result_paths:
                candidate = root / value
                reports_root = (root / ".home-control" / "reports").resolve()
                try:
                    resolved = candidate.resolve()
                    valid = reports_root in resolved.parents and resolved.is_file() and not is_linklike(candidate)
                except (OSError, RuntimeError):
                    valid = False
                if not valid:
                    warnings.append(f"{location}: completed result path is missing or outside reports")
            normalized_results = {Path(value).as_posix() for value in result_paths}
            for value in normalized_results:
                suffix = Path(value).suffix.lower()
                if suffix not in {".md", ".pdf"}:
                    continue
                paired_suffix = ".pdf" if suffix == ".md" else ".md"
                paired = Path(value).with_suffix(paired_suffix).as_posix()
                if paired not in normalized_results:
                    warnings.append(
                        f"{location}: completed Markdown/PDF conclusion is missing paired result path {paired}"
                    )
        if version == 1 and str(request.get("supersedes_analysis_request_id", "")).strip():
            warnings.append(f"{location}: request version 1 must not supersede another revision")
        if isinstance(version, int) and version > 1:
            if not isinstance(request.get("revised_at"), str) or not request["revised_at"].strip():
                warnings.append(f"{location}: revised_at is required after version 1")

    requests_by_id = records_by_id(jsonl_records["analysis_requests.jsonl"], "analysis_request_id")
    for series_id, revisions in requests_by_series.items():
        by_version = {request.get("request_version"): request for request in revisions}
        if len(by_version) != len(revisions):
            warnings.append(f"analysis_requests.jsonl: duplicate request_version in series {series_id}")
            continue
        expected_versions = set(range(1, len(revisions) + 1))
        if set(by_version) != expected_versions:
            warnings.append(f"analysis_requests.jsonl: non-contiguous versions in series {series_id}")
            continue
        for version in range(2, len(revisions) + 1):
            current = by_version[version]
            prior_id = str(current.get("supersedes_analysis_request_id", "")).strip()
            prior = requests_by_id.get(prior_id)
            if prior is not by_version[version - 1]:
                warnings.append(
                    f"analysis_requests.jsonl: version {version} in series {series_id} must supersede version {version - 1}"
                )
            elif current.get("request_text") != prior.get("request_text"):
                warnings.append(
                    f"analysis_requests.jsonl: version {version} in series {series_id} must preserve the original request_text"
                )
            elif current.get("requested_at") != prior.get("requested_at"):
                warnings.append(
                    f"analysis_requests.jsonl: version {version} in series {series_id} must preserve the original requested_at"
                )
            elif current.get("request_type") != prior.get("request_type"):
                warnings.append(
                    f"analysis_requests.jsonl: version {version} in series {series_id} must preserve the original request_type"
                )


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


def normalized_string_set(value: object) -> set[str] | None:
    if not isinstance(value, list):
        return None
    result: list[str] = []
    for item in value:
        if not isinstance(item, str) or not item.strip():
            return None
        result.append(item.strip())
    if len(result) != len(set(result)):
        return None
    return set(result)


def normalized_package_pairs(value: object) -> set[tuple[str, str]] | None:
    if not isinstance(value, list):
        return None
    result: list[tuple[str, str]] = []
    for pair in value:
        if (
            not isinstance(pair, list)
            or len(pair) != 2
            or any(not isinstance(item, str) or not item.strip() for item in pair)
            or pair[0].strip() == pair[1].strip()
        ):
            return None
        result.append(tuple(sorted((pair[0].strip(), pair[1].strip()))))
    if len(result) != len(set(result)):
        return None
    return set(result)


def validate_analysis_layer(
    jsonl_records: dict[str, list[tuple[int, dict]]],
    jsonl_ids: dict[str, set[str]],
    active_documents: set[str],
    document_versions: dict[str, set[tuple[object, str]]],
    complete_read_versions: set[tuple[str, object, str]],
    warnings: list[str],
) -> None:
    facts = jsonl_ids["facts.jsonl"]
    requirements = jsonl_ids["approved_requirements.jsonl"]
    packages = jsonl_ids["project_packages.jsonl"]
    gaps = jsonl_ids["information_gaps.jsonl"]
    resources = jsonl_ids["shared_resources.jsonl"]
    demands = jsonl_ids["resource_demands.jsonl"]
    sites = jsonl_ids["sites.jsonl"]
    zones = jsonl_ids["zones.jsonl"]
    systems = jsonl_ids["systems.jsonl"]
    decisions = jsonl_ids["decisions.jsonl"]
    reading_runs = records_by_id(jsonl_records["reading_runs.jsonl"], "reading_run_id")
    all_registered_ids: set[str] = set(active_documents)
    for identifiers in jsonl_ids.values():
        all_registered_ids.update(identifiers)

    def linked_ids(record: dict, field: str, known: set[str], location: str) -> list[str]:
        values = id_list(record, field, location, warnings)
        if any(value not in known for value in values):
            warnings.append(f"{location}: {field} contains an unknown link")
        return values

    for line_number, record in jsonl_records["project_packages.jsonl"]:
        location = f"project_packages.jsonl:{line_number}"
        for field in ("name", "goal"):
            if not isinstance(record.get(field), str) or not record[field].strip():
                warnings.append(f"{location}: missing required field {field}")
        if record.get("status") not in DIMENSIONS["project_package_status"]:
            warnings.append(f"{location}: unknown project package status")
        disciplines = normalized_string_set(record.get("disciplines"))
        if not disciplines:
            warnings.append(f"{location}: disciplines must be a non-empty unique string array")
        linked_ids(record, "fact_ids", facts, location)
        linked_ids(record, "requirement_ids", requirements, location)
        linked_ids(record, "information_gap_ids", gaps, location)
        linked_ids(record, "site_ids", sites, location)
        linked_ids(record, "zone_ids", zones, location)
        linked_ids(record, "system_ids", systems, location)
        versions = record.get("source_document_versions")
        if not isinstance(versions, list):
            warnings.append(f"{location}: source_document_versions must be an array")
        else:
            for number, version in enumerate(versions, 1):
                if not isinstance(version, dict):
                    warnings.append(f"{location}: source document version {number} must be an object")
                    continue
                document_id = str(version.get("document_id", "")).strip()
                version_key = (version.get("document_version"), str(version.get("sha256", "")).strip())
                if document_id not in active_documents or version_key not in document_versions.get(document_id, set()):
                    warnings.append(f"{location}: source document version {number} is not an exact active version")

    for line_number, record in jsonl_records["fact_extraction_runs.jsonl"]:
        location = f"fact_extraction_runs.jsonl:{line_number}"
        status = record.get("status")
        if status not in DIMENSIONS["fact_extraction_status"]:
            warnings.append(f"{location}: unknown fact extraction status")
        document_id = str(record.get("source_document_id", "")).strip()
        version_key = (record.get("document_version"), str(record.get("sha256", "")).strip())
        exact_key = (document_id, version_key[0], version_key[1])
        if document_id not in active_documents or version_key not in document_versions.get(document_id, set()):
            warnings.append(f"{location}: extraction is not bound to an exact active document version")
        reading_run_id = str(record.get("reading_run_id", "")).strip()
        reading_run = reading_runs.get(reading_run_id)
        if reading_run is None:
            warnings.append(f"{location}: unknown reading_run_id")
        elif (
            reading_run.get("source_document_id") != document_id
            or reading_run.get("document_version") != version_key[0]
            or str(reading_run.get("sha256", "")).strip() != version_key[1]
        ):
            warnings.append(f"{location}: reading run belongs to a different document version")
        fact_ids = linked_ids(record, "fact_ids", facts, location)
        linked_ids(record, "requirement_ids", requirements, location)
        linked_ids(record, "information_gap_ids", gaps, location)
        linked_ids(record, "conflict_fact_ids", facts, location)
        expected = normalized_string_set(record.get("expected_sections"))
        checked = normalized_string_set(record.get("checked_sections"))
        coverage_gaps = record.get("coverage_gaps")
        if status == "complete":
            if exact_key not in complete_read_versions:
                warnings.append(f"{location}: complete extraction has no complete ReadingRun for the exact version")
            if not expected or checked != expected or not isinstance(coverage_gaps, list) or coverage_gaps:
                warnings.append(f"{location}: complete extraction has unresolved or inconsistent semantic coverage")
            if not fact_ids:
                warnings.append(f"{location}: complete extraction has no extracted facts")

    for line_number, record in jsonl_records["information_gaps.jsonl"]:
        location = f"information_gaps.jsonl:{line_number}"
        for field in ("description", "blocked_conclusion", "required_provider", "required_format"):
            if not isinstance(record.get(field), str) or not record[field].strip():
                warnings.append(f"{location}: missing required field {field}")
        status = record.get("status")
        if status not in DIMENSIONS["information_gap_status"]:
            warnings.append(f"{location}: unknown information gap status")
        linked_ids(record, "package_ids", packages, location)
        linked_ids(record, "blocked_entity_ids", all_registered_ids, location)
        answer_ids = linked_ids(record, "answer_source_ids", all_registered_ids, location)
        if status in {"answered", "verified"} and not answer_ids:
            warnings.append(f"{location}: answered or verified gap has no answer sources")

    for line_number, record in jsonl_records["shared_resources.jsonl"]:
        location = f"shared_resources.jsonl:{line_number}"
        if not isinstance(record.get("name"), str) or not record["name"].strip():
            warnings.append(f"{location}: missing required field name")
        if record.get("resource_type") not in DIMENSIONS["shared_resource_type"]:
            warnings.append(f"{location}: unknown shared resource type")
        site_id = str(record.get("site_id", "")).strip()
        if site_id and site_id not in sites:
            warnings.append(f"{location}: unknown site_id")
        linked_ids(record, "zone_ids", zones, location)
        linked_ids(record, "source_fact_ids", facts, location)

    for line_number, record in jsonl_records["resource_demands.jsonl"]:
        location = f"resource_demands.jsonl:{line_number}"
        if str(record.get("package_id", "")).strip() not in packages:
            warnings.append(f"{location}: unknown package_id")
        if str(record.get("resource_id", "")).strip() not in resources:
            warnings.append(f"{location}: unknown resource_id")
        if not isinstance(record.get("description"), str) or not record["description"].strip():
            warnings.append(f"{location}: missing required field description")
        if record.get("status") not in DIMENSIONS["resource_demand_status"]:
            warnings.append(f"{location}: unknown resource demand status")
        source_ids = linked_ids(record, "source_fact_ids", facts, location)
        gap_ids = linked_ids(record, "information_gap_ids", gaps, location)
        if not source_ids and not gap_ids:
            warnings.append(f"{location}: demand needs a source fact or an information gap")

    for line_number, record in jsonl_records["package_interfaces.jsonl"]:
        location = f"package_interfaces.jsonl:{line_number}"
        package_ids = linked_ids(record, "package_ids", packages, location)
        if len(set(package_ids)) != 2:
            warnings.append(f"{location}: package_ids must contain exactly two different packages")
        if record.get("interface_type") not in DIMENSIONS["package_interface_type"]:
            warnings.append(f"{location}: unknown package interface type")
        if not isinstance(record.get("description"), str) or not record["description"].strip():
            warnings.append(f"{location}: missing required field description")
        linked_ids(record, "resource_ids", resources, location)
        linked_ids(record, "source_fact_ids", facts, location)

    for line_number, record in jsonl_records["coordination_issues.jsonl"]:
        location = f"coordination_issues.jsonl:{line_number}"
        package_ids = linked_ids(record, "package_ids", packages, location)
        if len(set(package_ids)) < 2:
            warnings.append(f"{location}: coordination issue must link at least two packages")
        if record.get("issue_type") not in DIMENSIONS["coordination_issue_type"]:
            warnings.append(f"{location}: unknown coordination issue type")
        if record.get("status") not in DIMENSIONS["coordination_issue_status"]:
            warnings.append(f"{location}: unknown coordination issue status")
        if not isinstance(record.get("description"), str) or not record["description"].strip():
            warnings.append(f"{location}: missing required field description")
        linked_ids(record, "resource_ids", resources, location)
        linked_ids(record, "source_fact_ids", facts, location)
        linked_ids(record, "information_gap_ids", gaps, location)
        decision_id = str(record.get("owner_decision_id", "")).strip()
        if decision_id and decision_id not in decisions:
            warnings.append(f"{location}: unknown owner_decision_id")
        if record.get("status") in {"resolved", "accepted_risk"} and not (
            decision_id or normalized_string_set(record.get("resolution_source_ids"))
        ):
            warnings.append(f"{location}: resolved issue has no resolution evidence")

    for line_number, record in jsonl_records["coordination_runs.jsonl"]:
        location = f"coordination_runs.jsonl:{line_number}"
        status = record.get("status")
        if status not in DIMENSIONS["coordination_run_status"]:
            warnings.append(f"{location}: unknown coordination run status")
        package_ids = linked_ids(record, "package_ids", packages, location)
        linked_ids(record, "resource_demand_ids", demands, location)
        linked_ids(record, "issue_ids", jsonl_ids["coordination_issues.jsonl"], location)
        expected_pairs = normalized_package_pairs(record.get("expected_package_pairs"))
        checked_pairs = normalized_package_pairs(record.get("checked_package_pairs"))
        if expected_pairs is not None and any(item not in packages for pair in expected_pairs for item in pair):
            warnings.append(f"{location}: package-pair coverage contains an unknown package")
        if status == "complete" and (
            len(set(package_ids)) < 2
            or not expected_pairs
            or checked_pairs != expected_pairs
            or record.get("coverage_gaps") != []
        ):
            warnings.append(f"{location}: complete coordination run has unresolved or inconsistent coverage")


def validate_regulatory_layer(
    jsonl_records: dict[str, list[tuple[int, dict]]],
    jsonl_ids: dict[str, set[str]],
    active_documents: set[str],
    warnings: list[str],
) -> None:
    norms = jsonl_ids["norm_references.jsonl"]
    requirements = jsonl_ids["regulatory_requirements.jsonl"]
    assessments = jsonl_ids["compliance_assessments.jsonl"]
    results = jsonl_ids["compliance_results.jsonl"]
    gaps = jsonl_ids["information_gaps.jsonl"]
    facts = jsonl_ids["facts.jsonl"]
    findings = jsonl_ids["findings.jsonl"]
    requirement_records = records_by_id(
        jsonl_records["regulatory_requirements.jsonl"], "regulatory_requirement_id"
    )
    result_records = records_by_id(jsonl_records["compliance_results.jsonl"], "compliance_result_id")
    assessed_norm_ids: set[str] = set()
    for _, assessment in jsonl_records["compliance_assessments.jsonl"]:
        value = assessment.get("norm_reference_ids")
        if isinstance(value, list):
            assessed_norm_ids.update(
                item.strip() for item in value if isinstance(item, str) and item.strip()
            )
    all_registered_ids = set(active_documents)
    for identifiers in jsonl_ids.values():
        all_registered_ids.update(identifiers)

    def linked_ids(record: dict, field: str, known: set[str], location: str) -> list[str]:
        values = id_list(record, field, location, warnings)
        if any(value not in known for value in values):
            warnings.append(f"{location}: {field} contains an unknown link")
        return values

    for line_number, record in jsonl_records["norm_references.jsonl"]:
        location = f"norm_references.jsonl:{line_number}"
        norm_id = str(record.get("norm_reference_id", "")).strip()
        current_markers = (
            "designation",
            "document_kind",
            "document_status",
            "jurisdiction",
            "status_source_url",
        )
        if norm_id not in assessed_norm_ids and not any(field in record for field in current_markers):
            for field in ("title", "version", "territory", "checked_at", "locator", "source_url", "scope"):
                if not isinstance(record.get(field), str) or not record[field].strip():
                    warnings.append(f"{location}: incomplete legacy normative source context {field}")
            continue
        for field in (
            "designation",
            "title",
            "version",
            "jurisdiction",
            "territory",
            "checked_at",
            "status_source_url",
            "source_url",
            "scope",
        ):
            if not isinstance(record.get(field), str) or not record[field].strip():
                warnings.append(f"{location}: missing normative source context {field}")
        if record.get("document_kind") not in DIMENSIONS["norm_document_kind"]:
            warnings.append(f"{location}: unknown norm document kind")
        if record.get("document_status") not in DIMENSIONS["norm_document_status"]:
            warnings.append(f"{location}: unknown norm document status")
        linked_ids(record, "supersedes_norm_reference_ids", norms, location)
        linked_ids(record, "replacement_norm_reference_ids", norms, location)

    for line_number, record in jsonl_records["regulatory_requirements.jsonl"]:
        location = f"regulatory_requirements.jsonl:{line_number}"
        norm_id = str(record.get("norm_reference_id", "")).strip()
        if norm_id not in norms:
            warnings.append(f"{location}: unknown norm_reference_id")
        for field in (
            "locator",
            "statement",
            "scope_conditions",
            "verification_method",
            "specialist_boundary",
            "source_url",
            "extracted_at",
        ):
            if not isinstance(record.get(field), str) or not record[field].strip():
                warnings.append(f"{location}: missing required field {field}")
        if record.get("verification_status") not in DIMENSIONS["verification_status"]:
            warnings.append(f"{location}: missing or unknown verification_status")

    for line_number, record in jsonl_records["compliance_results.jsonl"]:
        location = f"compliance_results.jsonl:{line_number}"
        assessment_id = str(record.get("assessment_id", "")).strip()
        requirement_id = str(record.get("requirement_id", "")).strip()
        if assessment_id not in assessments:
            warnings.append(f"{location}: unknown assessment_id")
        if requirement_id not in requirements:
            warnings.append(f"{location}: unknown requirement_id")
        if record.get("applicability_status") not in DIMENSIONS["regulatory_applicability_status"]:
            warnings.append(f"{location}: unknown applicability_status")
        compliance_status = record.get("compliance_status")
        if compliance_status not in DIMENSIONS["regulatory_compliance_status"]:
            warnings.append(f"{location}: unknown compliance_status")
        for field in ("basis", "checked_at"):
            if not isinstance(record.get(field), str) or not record[field].strip():
                warnings.append(f"{location}: missing required field {field}")
        target_ids = linked_ids(record, "target_entity_ids", all_registered_ids, location)
        if not target_ids:
            warnings.append(f"{location}: target_entity_ids must not be empty")
        evidence_ids = linked_ids(record, "evidence_fact_ids", facts, location)
        finding_ids = linked_ids(record, "finding_ids", findings, location)
        gap_ids = linked_ids(record, "information_gap_ids", gaps, location)
        if compliance_status in {"conforms", "partially_conforms", "conflicts"} and not (
            evidence_ids or finding_ids
        ):
            warnings.append(f"{location}: compliance conclusion has no fact or finding evidence")
        if compliance_status == "not_verified" and not gap_ids:
            warnings.append(f"{location}: not_verified result has no information gap")
        if compliance_status == "not_applicable" and record.get("applicability_status") != "not_applicable":
            warnings.append(f"{location}: not_applicable result needs not_applicable applicability")
        if record.get("applicability_status") == "not_applicable" and compliance_status != "not_applicable":
            warnings.append(f"{location}: not_applicable applicability needs a not_applicable result")
        if record.get("applicability_status") == "undetermined" and compliance_status not in {
            "not_verified",
            "requires_specialist",
        }:
            warnings.append(f"{location}: undetermined applicability cannot support a conformity conclusion")

    for line_number, record in jsonl_records["compliance_assessments.jsonl"]:
        location = f"compliance_assessments.jsonl:{line_number}"
        for field in ("jurisdiction", "scope", "assessed_at"):
            if not isinstance(record.get(field), str) or not record[field].strip():
                warnings.append(f"{location}: missing required field {field}")
        if record.get("assessment_type") not in DIMENSIONS["regulatory_assessment_type"]:
            warnings.append(f"{location}: unknown assessment_type")
        status = record.get("status")
        if status not in DIMENSIONS["regulatory_assessment_status"]:
            warnings.append(f"{location}: unknown regulatory assessment status")
        target_ids = linked_ids(record, "target_entity_ids", all_registered_ids, location)
        if not target_ids:
            warnings.append(f"{location}: target_entity_ids must not be empty")
        norm_ids = linked_ids(record, "norm_reference_ids", norms, location)
        expected_ids = linked_ids(record, "expected_requirement_ids", requirements, location)
        checked_ids = linked_ids(record, "checked_requirement_ids", requirements, location)
        result_ids = linked_ids(record, "result_ids", results, location)
        linked_ids(record, "information_gap_ids", gaps, location)
        limitations = record.get("limitations")
        if not isinstance(limitations, list) or any(
            not isinstance(value, str) or not value.strip() for value in limitations
        ):
            warnings.append(f"{location}: limitations must be an array of non-empty strings")
        if status == "complete":
            if not norm_ids or not expected_ids:
                warnings.append(f"{location}: complete assessment has no normative scope")
            if set(checked_ids) != set(expected_ids) or len(checked_ids) != len(set(checked_ids)):
                warnings.append(f"{location}: complete assessment has incomplete requirement coverage")
            linked_results = [result_records[value] for value in result_ids if value in result_records]
            for requirement_id in expected_ids:
                requirement = requirement_records.get(requirement_id)
                if requirement and requirement.get("norm_reference_id") not in norm_ids:
                    warnings.append(f"{location}: expected requirement is outside norm_reference_ids")
            assessment_targets = set(target_ids)
            for linked_result in linked_results:
                result_targets = {
                    value.strip()
                    for value in linked_result.get("target_entity_ids", [])
                    if isinstance(value, str) and value.strip()
                } if isinstance(linked_result.get("target_entity_ids"), list) else set()
                if not result_targets.issubset(assessment_targets):
                    warnings.append(f"{location}: linked result target is outside assessment target_entity_ids")
            result_requirement_ids = [
                str(value.get("requirement_id", "")).strip()
                for value in linked_results
                if value.get("assessment_id") == record.get("compliance_assessment_id")
            ]
            if (
                len(linked_results) != len(result_ids)
                or sorted(result_requirement_ids) != sorted(expected_ids)
                or len(result_requirement_ids) != len(set(result_requirement_ids))
            ):
                warnings.append(f"{location}: complete assessment does not have one result per requirement")

    for line_number, record in jsonl_records["regulatory_sync_runs.jsonl"]:
        location = f"regulatory_sync_runs.jsonl:{line_number}"
        if record.get("status") not in DIMENSIONS["regulatory_sync_status"]:
            warnings.append(f"{location}: unknown regulatory sync status")
        if not isinstance(record.get("checked_at"), str) or not record["checked_at"].strip():
            warnings.append(f"{location}: missing checked_at")
        source_urls = normalized_string_set(record.get("source_urls"))
        if not source_urls:
            warnings.append(f"{location}: source_urls must be a non-empty unique string array")
        linked_ids(record, "norm_reference_ids", norms, location)
        linked_ids(record, "detected_change_norm_reference_ids", norms, location)
        source_checks = record.get("source_checks")
        if not isinstance(source_checks, list) or not source_checks:
            warnings.append(f"{location}: source_checks must be a non-empty array")
            continue
        checked_urls: list[str] = []
        change_statuses: list[str] = []
        for number, check in enumerate(source_checks, 1):
            if not isinstance(check, dict):
                warnings.append(f"{location}: source check {number} must be an object")
                continue
            for field in ("source_id", "url", "checked_at", "change_status"):
                if not isinstance(check.get(field), str) or not check[field].strip():
                    warnings.append(f"{location}: source check {number} missing {field}")
            checked_urls.append(str(check.get("url", "")).strip())
            change_statuses.append(str(check.get("change_status", "")).strip())
            if check.get("change_status") not in {"unchanged", "changed", "baseline_created", "error"}:
                warnings.append(f"{location}: source check {number} has unknown change_status")
            if check.get("change_status") != "error" and not str(check.get("content_sha256", "")).strip():
                warnings.append(f"{location}: source check {number} has no content_sha256")
        if len(checked_urls) != len(set(checked_urls)):
            warnings.append(f"{location}: source_checks repeat a URL")
        if record.get("status") == "complete":
            if source_urls is not None and set(checked_urls) != source_urls:
                warnings.append(f"{location}: complete sync does not cover every source URL")
            if "error" in change_statuses:
                warnings.append(f"{location}: complete sync contains a failed source check")


def validate_fact_records(
    jsonl_records: dict[str, list[tuple[int, dict]]],
    jsonl_ids: dict[str, set[str]],
    active_documents: set[str],
    document_versions: dict[str, set[tuple[object, str]]],
    warnings: list[str],
) -> None:
    facts_by_id = records_by_id(jsonl_records["facts.jsonl"], "fact_id")
    decisions_by_id = records_by_id(jsonl_records["decisions.jsonl"], "decision_id")
    update_graph: dict[str, set[str]] = {}
    for line_number, record in jsonl_records["facts.jsonl"]:
        location = f"facts.jsonl:{line_number}"
        for field in ("statement_kind", "evidence_origin", "verification_status"):
            value = str(record.get(field, "")).strip()
            if not value:
                warnings.append(f"{location}: missing ontology field {field}")
            elif value not in DIMENSIONS[field]:
                warnings.append(f"{location}: unknown {field} {value}")
        for field in ("statement", "locator", "recorded_at"):
            if not isinstance(record.get(field), str) or not record[field].strip():
                warnings.append(f"{location}: missing required field {field}")
        fact_id = str(record.get("fact_id", "")).strip()
        supersedes = normalized_string_set(record.get("supersedes_fact_ids", []))
        if supersedes is None:
            warnings.append(f"{location}: supersedes_fact_ids must be a unique string array")
            supersedes = set()
        update_kind = str(record.get("update_kind", "")).strip()
        update_reason = str(record.get("update_reason", "")).strip()
        if supersedes:
            if fact_id in supersedes:
                warnings.append(f"{location}: a fact cannot supersede itself")
            if any(value not in facts_by_id for value in supersedes):
                warnings.append(f"{location}: supersedes_fact_ids contains an unknown fact")
            if update_kind not in DIMENSIONS["fact_update_kind"]:
                warnings.append(f"{location}: linked fact update requires a known update_kind")
            if not update_reason:
                warnings.append(f"{location}: linked fact update requires update_reason")
        elif update_kind or update_reason:
            warnings.append(f"{location}: update_kind and update_reason require supersedes_fact_ids")
        if fact_id:
            update_graph[fact_id] = set(supersedes)
        conflicts = normalized_string_set(record.get("conflicts_with_fact_ids", []))
        if conflicts is None:
            warnings.append(f"{location}: conflicts_with_fact_ids must be a unique string array")
            conflicts = set()
        if fact_id in conflicts:
            warnings.append(f"{location}: a fact cannot conflict with itself")
        if any(value not in facts_by_id for value in conflicts):
            warnings.append(f"{location}: conflicts_with_fact_ids contains an unknown fact")
        if conflicts and record.get("verification_status") != "conflicted":
            warnings.append(f"{location}: an unresolved fact conflict requires verification_status conflicted")
        resolution_decision_id = str(record.get("conflict_resolution_decision_id", "")).strip()
        if resolution_decision_id:
            decision = decisions_by_id.get(resolution_decision_id)
            if (
                decision is None
                or decision.get("decision_type") != "fact_conflict_resolution"
                or decision.get("status") != "approved"
                or decision.get("approved_by") != "owner"
            ):
                warnings.append(f"{location}: conflict_resolution_decision_id is not an approved owner resolution")
            elif not supersedes.issubset(
                normalized_string_set(decision.get("source_fact_ids", [])) or set()
            ):
                warnings.append(f"{location}: owner conflict resolution does not cite every superseded fact")
            if not supersedes:
                warnings.append(f"{location}: a conflict resolution fact must supersede the resolved fact records")
        disciplines = normalized_string_set(record.get("discipline_ids"))
        if disciplines is None:
            warnings.append(f"{location}: discipline_ids must be a unique string array")
        for field, registry in (
            ("package_ids", "project_packages.jsonl"),
            ("site_ids", "sites.jsonl"),
            ("zone_ids", "zones.jsonl"),
            ("system_ids", "systems.jsonl"),
        ):
            if not isinstance(record.get(field), list):
                warnings.append(f"{location}: required field {field} must be an array")
            else:
                linked = id_list(record, field, location, warnings)
                if any(identifier not in jsonl_ids[registry] for identifier in linked):
                    warnings.append(f"{location}: {field} contains an unknown link")
        source_id = str(record.get("source_document_id", "")).strip()
        evidence_origin = str(record.get("evidence_origin", "")).strip()
        if evidence_origin == "witness_statement":
            if record.get("statement_kind") != "observation":
                warnings.append(f"{location}: witness statement must use statement_kind observation")
            for field in ("witness_reference", "reported_at"):
                if not isinstance(record.get(field), str) or not record[field].strip():
                    warnings.append(f"{location}: witness statement is missing {field}")
            corroborating = normalized_string_set(record.get("corroborating_source_ids"))
            if corroborating is None:
                warnings.append(f"{location}: corroborating_source_ids must be a unique string array")
                corroborating = set()
            known_ids = set(active_documents)
            for identifiers in jsonl_ids.values():
                known_ids.update(identifiers)
            if any(identifier not in known_ids for identifier in corroborating):
                warnings.append(f"{location}: witness statement has an unknown corroborating source")
            if record.get("verification_status") == "verified" and not corroborating:
                warnings.append(f"{location}: verified witness statement has no corroborating source")
        if source_id and source_id not in active_documents:
            warnings.append(f"{location}: source document is not active: {source_id}")
        if source_id:
            version_key = (record.get("document_version"), str(record.get("sha256", "")).strip())
            if version_key not in document_versions.get(source_id, set()):
                warnings.append(f"{location}: fact is not bound to an exact registered source version")
        elif evidence_origin not in {"owner_confirmation", "witness_statement", "agreed_assumption"} and not str(
            record.get("source_url", "")
        ).strip():
            warnings.append(f"{location}: fact has no source document, owner basis or external source URL")

    visiting: set[str] = set()
    visited: set[str] = set()

    def visit(fact_id: str) -> bool:
        if fact_id in visiting:
            return True
        if fact_id in visited:
            return False
        visiting.add(fact_id)
        has_cycle = any(prior in update_graph and visit(prior) for prior in update_graph.get(fact_id, set()))
        visiting.remove(fact_id)
        visited.add(fact_id)
        return has_cycle

    if any(visit(fact_id) for fact_id in update_graph):
        warnings.append("facts.jsonl: supersedes_fact_ids contains a cycle")


def validate_review_contract(
    review: dict,
    registered_ids: set[str],
    known_alternatives: set[str],
    known_findings: set[str] | None = None,
    fact_verification_statuses: dict[str, str] | None = None,
) -> list[str]:
    """Return machine-checkable contract errors for one ProposalReview."""
    errors: list[str] = []
    statuses = set(PROPOSAL_CONTRACT["check_statuses"])
    ready_statuses = set(PROPOSAL_CONTRACT["ready_statuses"])
    verification_statuses = set(DIMENSIONS["verification_status"])
    expected_mandatory = [value["check_id"] for value in PROPOSAL_CONTRACT["universal_checks"]]
    non_waivable_mandatory = set(PROPOSAL_CONTRACT.get("non_waivable_universal_check_ids", []))
    expected_axes = [value["axis_id"] for value in PROPOSAL_CONTRACT["discipline_axes"]]
    expected_tracks = [value["track_id"] for value in PROPOSAL_CONTRACT["technical_alternative_tracks"]]
    expected_roles = set(PROPOSAL_CONTRACT.get("scope_responsibility_roles", []))
    expected_phases = [value["phase_id"] for value in PROPOSAL_CONTRACT.get("constructability_phases", [])]
    expected_contractor_axes = [
        value["axis_id"] for value in PROPOSAL_CONTRACT.get("contractor_assessment_axes", [])
    ]
    disciplines = review.get("disciplines") if isinstance(review.get("disciplines"), list) else []
    manifest = review.get("completion_manifest")
    recorded_version = manifest.get("contract_version") if isinstance(manifest, dict) else None
    current_version = PROPOSAL_CONTRACT["contract_version"]
    legacy_versions = set(PROPOSAL_CONTRACT.get("legacy_contract_versions", []))
    current_contract = recorded_version == current_version
    if recorded_version not in {current_version, *legacy_versions}:
        errors.append("completion_manifest has an unknown contract_version")

    def objects(field: str) -> list[dict]:
        value = review.get(field)
        if not isinstance(value, list) or any(not isinstance(item, dict) for item in value):
            errors.append(f"{field} must be an array of objects")
            return []
        return value

    def strings(value: object, label: str, allow_empty: bool = True) -> list[str]:
        if not isinstance(value, list) or any(not isinstance(item, str) or not item.strip() for item in value):
            errors.append(f"{label} must be a string array")
            return []
        cleaned = [item.strip() for item in value]
        if not allow_empty and not cleaned:
            errors.append(f"{label} must not be empty")
        return cleaned

    def validate_sources(value: object, label: str, allow_empty: bool = False) -> list[str]:
        sources = strings(value, f"{label} source_ids", allow_empty=allow_empty)
        if any(source not in registered_ids for source in sources):
            errors.append(f"{label} refers to an unknown source")
        return sources

    def validate_observations(item: dict, label: str) -> None:
        observations = item.get("observations")
        if not isinstance(observations, list) or not observations or any(
            not isinstance(value, dict) for value in observations
        ):
            errors.append(f"{label} requires evidence observations")
            return
        for number, observation in enumerate(observations, 1):
            observation_label = f"{label} observation {number}"
            if not str(observation.get("statement", "")).strip():
                errors.append(f"{observation_label} has no statement")
            if not str(observation.get("locator", "")).strip():
                errors.append(f"{observation_label} has no locator")
            if observation.get("verification_status") not in verification_statuses:
                errors.append(f"{observation_label} has an unknown verification_status")
            validate_sources(observation.get("source_ids", []), observation_label)

    def validate_item(item: dict, label: str, require_sources: bool = True) -> str:
        status = str(item.get("status", "")).strip()
        if status not in statuses:
            errors.append(f"{label} has an unknown status")
        result = str(item.get("result", "")).strip()
        rationale = str(item.get("rationale", "")).strip()
        if not result:
            errors.append(f"{label} has no result")
        if status in {"not_applicable", "blocked", "requires_specialist"} and not rationale:
            errors.append(f"{label} requires a rationale")
        sources = validate_sources(item.get("source_ids", []), label, allow_empty=True)
        if require_sources and status == "completed" and not sources:
            errors.append(f"{label} completed without sources")
        if current_contract:
            if status == "completed":
                validate_observations(item, label)
            elif status == "not_applicable":
                if not str(item.get("applicability_evidence", "")).strip():
                    errors.append(f"{label} not_applicable without applicability_evidence")
                if not sources:
                    errors.append(f"{label} not_applicable without sources")
            elif status in {"blocked", "requires_specialist"}:
                strings(item.get("required_inputs", []), f"{label} required_inputs", allow_empty=False)
        return status

    mandatory = objects("mandatory_checks")
    mandatory_ids: list[str] = []
    required_statuses: list[str] = []
    for item in mandatory:
        identifier = str(item.get("check_id", "")).strip()
        mandatory_ids.append(identifier)
        item_status = validate_item(item, f"mandatory check {identifier or 'without ID'}")
        required_statuses.append(item_status)
        if current_contract and identifier in non_waivable_mandatory and item_status == "not_applicable":
            errors.append(f"mandatory check {identifier} cannot be not_applicable")
    if sorted(mandatory_ids) != sorted(expected_mandatory) or len(mandatory_ids) != len(set(mandatory_ids)):
        errors.append("mandatory_checks do not cover the current universal contract exactly once")

    discipline_checks = objects("discipline_checks")
    discipline_keys: list[str] = []
    for item in discipline_checks:
        discipline = str(item.get("discipline", "")).strip()
        axis = str(item.get("axis_id", "")).strip()
        key = f"{discipline}|{axis}"
        discipline_keys.append(key)
        if discipline not in disciplines or axis not in expected_axes:
            errors.append(f"discipline check {key} is outside the declared contract")
        item_status = validate_item(item, f"discipline check {key}")
        required_statuses.append(item_status)
        if current_contract and item_status == "completed":
            strings(item.get("criteria_checked", []), f"discipline check {key} criteria_checked", allow_empty=False)
            strings(item.get("field_risks", []), f"discipline check {key} field_risks")
            strings(item.get("required_site_checks", []), f"discipline check {key} required_site_checks")
    expected_discipline_keys = [f"{discipline}|{axis}" for discipline in disciplines for axis in expected_axes]
    if sorted(discipline_keys) != sorted(expected_discipline_keys) or len(discipline_keys) != len(set(discipline_keys)):
        errors.append("discipline_checks do not cover every required axis for every discipline exactly once")

    alternatives = objects("technical_alternative_assessments")
    track_ids: list[str] = []
    alternatives_by_id: dict[str, list[dict]] = {}
    for item in alternatives:
        track_id = str(item.get("track_id", "")).strip()
        track_ids.append(track_id)
        status = validate_item(item, f"technical alternative {track_id or 'without ID'}")
        required_statuses.append(status)
        linked = item.get("alternative_ids", [])
        if not isinstance(linked, list) or any(not isinstance(value, str) or not value.strip() for value in linked):
            errors.append(f"technical alternative {track_id} alternative_ids must be a string array")
            linked = []
        if any(value not in known_alternatives for value in linked):
            errors.append(f"technical alternative {track_id} refers to an unknown Alternative")
        if status == "completed":
            for field in (
                "solution",
                "project_fit",
                "benefits",
                "drawbacks",
                "implementation_impacts",
                "lifecycle_cost_notes",
                "performance_basis",
                "cost_basis",
                "constructability_basis",
                "recommendation",
            ):
                if not str(item.get(field, "")).strip():
                    errors.append(f"technical alternative {track_id} lacks {field}")
            if current_contract and not linked:
                errors.append(f"technical alternative {track_id} completed without a linked Alternative")
            for alternative_id in linked:
                alternatives_by_id.setdefault(alternative_id, []).append(item)
    if sorted(track_ids) != sorted(expected_tracks) or len(track_ids) != len(set(track_ids)):
        errors.append("technical_alternative_assessments do not cover every required track exactly once")
    if current_contract:
        for alternative_id, linked_items in alternatives_by_id.items():
            completed_items = [item for item in linked_items if item.get("status") == "completed"]
            if len(completed_items) > 1 and any(
                not str(item.get("shared_alternative_justification", "")).strip() for item in completed_items
            ):
                errors.append(
                    f"Alternative {alternative_id} is reused across technical tracks without justification"
                )

    additional = objects("additional_model_checks")
    additional_ids: list[str] = []
    for item in additional:
        identifier = str(item.get("check_id", "")).strip()
        additional_ids.append(identifier)
        if not identifier or not str(item.get("question", "")).strip():
            errors.append("additional model check requires check_id and question")
        validate_item(item, f"additional model check {identifier or 'without ID'}")
    if len(additional_ids) != len(set(additional_ids)):
        errors.append("additional_model_checks contain duplicate check_id values")

    scope_ids: list[str] = []
    phase_ids: list[str] = []
    contractor_axis_ids: list[str] = []
    site_verification_ids: list[str] = []
    acceptance_plan_ids: list[str] = []
    priority_risk_ids: list[str] = []
    baseline_scope_requirement_ids: list[str] = []
    as_is_match_fact_ids: list[str] = []
    context_conflict_ids: list[str] = []
    decision_criterion_ids: list[str] = []
    alternative_comparison_ids: list[str] = []
    price_comparison_ids: list[str] = []
    clarification_ids: list[str] = []
    coordination_run_ids: list[str] = []
    management_scenario_ids: list[str] = []
    challenge_completed = False
    site_statuses: list[str] = []
    contractor_statuses: list[str] = []
    cost_contract_blocked = False
    context_contract_blocked = False
    clarification_contract_blocked = False
    management_contract_blocked = False

    if current_contract:
        if not str(review.get("additional_analysis_summary", "")).strip():
            errors.append("additional_analysis_summary is required for the current contract")

        for field, allow_empty in (
            ("fact_extraction_run_ids", False),
            ("project_package_ids", False),
            ("information_gap_ids", True),
            ("coordination_issue_ids", True),
        ):
            linked = strings(review.get(field), field, allow_empty=allow_empty)
            if any(identifier not in registered_ids for identifier in linked):
                errors.append(f"{field} contains an unknown registered entity")

        foreman = review.get("foreman_assessment")
        if not isinstance(foreman, dict):
            errors.append("foreman_assessment must be an object")
            foreman = {}
        if foreman.get("verdict") not in PROPOSAL_CONTRACT["foreman_verdicts"]:
            errors.append("foreman_assessment has an unknown verdict")
        readiness = foreman.get("decision_readiness")
        if readiness not in PROPOSAL_CONTRACT["decision_readiness_statuses"]:
            errors.append("foreman_assessment has an unknown decision_readiness")
        if not str(foreman.get("summary", "")).strip():
            errors.append("foreman_assessment has no summary")
        if not str(foreman.get("decision_request", "")).strip():
            errors.append("foreman_assessment has no exact decision_request")
        preferred_alternative_id = str(foreman.get("preferred_alternative_id", "")).strip()
        if preferred_alternative_id and preferred_alternative_id not in known_alternatives:
            errors.append("foreman_assessment refers to an unknown preferred_alternative_id")
        if not str(foreman.get("preferred_alternative_rationale", "")).strip():
            errors.append("foreman_assessment has no preferred_alternative_rationale")
        strings(foreman.get("decisive_reasons", []), "foreman_assessment.decisive_reasons", allow_empty=False)
        strings(foreman.get("conditions_before_contract", []), "foreman_assessment.conditions_before_contract")
        strings(foreman.get("conditions_before_work", []), "foreman_assessment.conditions_before_work")
        strings(foreman.get("owner_next_actions", []), "foreman_assessment.owner_next_actions", allow_empty=False)
        validate_sources(foreman.get("source_ids", []), "foreman_assessment")

        scope_rows = objects("scope_boundary_matrix")
        for row in scope_rows:
            scope_id = str(row.get("scope_id", "")).strip()
            scope_ids.append(scope_id)
            if not scope_id or not str(row.get("result", "")).strip():
                errors.append("scope boundary row requires scope_id and result")
            responsibilities = row.get("responsibilities")
            if not isinstance(responsibilities, dict) or set(responsibilities) != expected_roles or any(
                not isinstance(value, str) or not value.strip() for value in responsibilities.values()
            ):
                errors.append(f"scope boundary {scope_id or 'without ID'} must assign every responsibility role")
            strings(row.get("quote_item_ids", []), f"scope boundary {scope_id} quote_item_ids", allow_empty=False)
            strings(row.get("requirement_ids", []), f"scope boundary {scope_id} requirement_ids")
            strings(row.get("gaps", []), f"scope boundary {scope_id} gaps")
            validate_sources(row.get("source_ids", []), f"scope boundary {scope_id}")
        if not scope_rows or len(scope_ids) != len(set(scope_ids)) or any(not value for value in scope_ids):
            errors.append("scope_boundary_matrix requires unique non-empty scope rows")

        phases = objects("constructability_walkthrough")
        for phase in phases:
            phase_id = str(phase.get("phase_id", "")).strip()
            phase_ids.append(phase_id)
            phase_status = validate_item(phase, f"constructability phase {phase_id or 'without ID'}")
            required_statuses.append(phase_status)
            strings(phase.get("risks", []), f"constructability phase {phase_id} risks")
            strings(phase.get("actions", []), f"constructability phase {phase_id} actions")
        if sorted(phase_ids) != sorted(expected_phases) or len(phase_ids) != len(set(phase_ids)):
            errors.append("constructability_walkthrough does not cover every required phase exactly once")

        cost = review.get("cost_exposure")
        if not isinstance(cost, dict):
            errors.append("cost_exposure must be an object")
            cost = {}
        if not str(cost.get("currency", "")).strip() or not str(cost.get("formula", "")).strip():
            errors.append("cost_exposure requires currency and formula")
        if cost.get("status") not in DIMENSIONS["calculation_status"]:
            errors.append("cost_exposure has an unknown status")
        amount_fields = (
            "quoted_total",
            "confirmed_included_amount",
            "known_excluded_amount",
            "estimated_total_low",
            "estimated_total_high",
        )
        for field in amount_fields:
            if field not in cost:
                errors.append(f"cost_exposure lacks {field}")
                continue
            value = cost.get(field)
            if value is not None and (
                isinstance(value, bool) or not isinstance(value, (int, float)) or value < 0
            ):
                errors.append(f"cost_exposure.{field} must be a non-negative number or null")
        low = cost.get("estimated_total_low")
        high = cost.get("estimated_total_high")
        if isinstance(low, (int, float)) and not isinstance(low, bool) and isinstance(high, (int, float)) and not isinstance(high, bool) and low > high:
            errors.append("cost_exposure estimated range is reversed")
        validate_sources(cost.get("source_ids", []), "cost_exposure")
        unknown_exposures = cost.get("unknown_exposures")
        if not isinstance(unknown_exposures, list) or any(not isinstance(value, dict) for value in unknown_exposures):
            errors.append("cost_exposure.unknown_exposures must be an array of objects")
            unknown_exposures = []
        for number, exposure in enumerate(unknown_exposures, 1):
            if not str(exposure.get("description", "")).strip() or not str(exposure.get("reason", "")).strip():
                errors.append(f"cost exposure unknown item {number} requires description and reason")
            if not isinstance(exposure.get("blocking"), bool):
                errors.append(f"cost exposure unknown item {number} requires a blocking flag")
            validate_sources(exposure.get("source_ids", []), f"cost exposure unknown item {number}")
        cost_contract_blocked = (
            cost.get("status") != "verified"
            or any(exposure.get("blocking") is True for exposure in unknown_exposures)
            or not isinstance(low, (int, float))
            or isinstance(low, bool)
            or not isinstance(high, (int, float))
            or isinstance(high, bool)
        )

        contractor_checks = objects("contractor_assessment")
        for item in contractor_checks:
            axis_id = str(item.get("axis_id", "")).strip()
            contractor_axis_ids.append(axis_id)
            item_status = validate_item(item, f"contractor assessment {axis_id or 'without ID'}")
            contractor_statuses.append(item_status)
            required_statuses.append(item_status)
        if sorted(contractor_axis_ids) != sorted(expected_contractor_axes) or len(contractor_axis_ids) != len(set(contractor_axis_ids)):
            errors.append("contractor_assessment does not cover every required axis exactly once")

        site_items = objects("site_verification_plan")
        for item in site_items:
            verification_id = str(item.get("verification_id", "")).strip()
            site_verification_ids.append(verification_id)
            status = str(item.get("status", "")).strip()
            site_statuses.append(status)
            if status not in PROPOSAL_CONTRACT["site_verification_statuses"]:
                errors.append(f"site verification {verification_id or 'without ID'} has an unknown status")
            for field in ("subject", "method", "responsible_role", "required_before", "consequence_if_unverified"):
                if not str(item.get(field, "")).strip():
                    errors.append(f"site verification {verification_id or 'without ID'} lacks {field}")
            if status == "completed":
                validate_sources(item.get("source_ids", []), f"site verification {verification_id}")
            elif status == "not_applicable":
                if not str(item.get("rationale", "")).strip() or not str(item.get("applicability_evidence", "")).strip():
                    errors.append(f"site verification {verification_id} not_applicable without evidence")
            elif status in {"blocked", "requires_specialist"}:
                strings(item.get("required_inputs", []), f"site verification {verification_id} required_inputs", allow_empty=False)
        if not site_items or len(site_verification_ids) != len(set(site_verification_ids)) or any(
            not value for value in site_verification_ids
        ):
            errors.append("site_verification_plan requires unique non-empty items")

        acceptance_items = objects("acceptance_plan")
        for item in acceptance_items:
            acceptance_id = str(item.get("acceptance_id", "")).strip()
            acceptance_plan_ids.append(acceptance_id)
            for field in ("result", "criterion", "method", "timing", "responsible_party"):
                if not str(item.get(field, "")).strip():
                    errors.append(f"acceptance plan {acceptance_id or 'without ID'} lacks {field}")
            strings(item.get("evidence_required", []), f"acceptance plan {acceptance_id} evidence_required", allow_empty=False)
            validate_sources(item.get("source_ids", []), f"acceptance plan {acceptance_id}")
        if not acceptance_items or len(acceptance_plan_ids) != len(set(acceptance_plan_ids)) or any(
            not value for value in acceptance_plan_ids
        ):
            errors.append("acceptance_plan requires unique non-empty items")

        risks = objects("priority_risks")
        for item in risks:
            risk_id = str(item.get("risk_id", "")).strip()
            priority_risk_ids.append(risk_id)
            finding_id = str(item.get("finding_id", "")).strip()
            if not risk_id or not finding_id:
                errors.append("priority risk requires risk_id and finding_id")
            if known_findings is not None and finding_id not in known_findings:
                errors.append(f"priority risk {risk_id or 'without ID'} refers to an unknown Finding")
            if item.get("urgency") not in PROPOSAL_CONTRACT["risk_urgencies"]:
                errors.append(f"priority risk {risk_id or 'without ID'} has an unknown urgency")
            impact_lanes = strings(item.get("impact_lanes", []), f"priority risk {risk_id} impact_lanes", allow_empty=False)
            if any(value not in PROPOSAL_CONTRACT["risk_impact_lanes"] for value in impact_lanes):
                errors.append(f"priority risk {risk_id} has an unknown impact lane")
            for field in ("consequence", "mitigation", "owner_action"):
                if not str(item.get(field, "")).strip():
                    errors.append(f"priority risk {risk_id or 'without ID'} lacks {field}")
            validate_sources(item.get("source_ids", []), f"priority risk {risk_id}")
        if len(priority_risk_ids) != len(set(priority_risk_ids)) or any(not value for value in priority_risk_ids):
            errors.append("priority_risks require unique non-empty risk_id values")
        if not str(review.get("risk_summary", "")).strip():
            errors.append("risk_summary is required for the current contract")

        series_id = str(review.get("review_series_id", "")).strip()
        revision = review.get("review_revision")
        if not series_id:
            errors.append("review_series_id is required for the current contract")
        if not isinstance(revision, int) or isinstance(revision, bool) or revision < 1:
            errors.append("review_revision must be a positive integer")
        if revision == 1 and str(review.get("supersedes_proposal_review_id", "")).strip():
            errors.append("review revision 1 must not supersede another review")

        context_mode = review.get("context_mode")
        if context_mode not in PROPOSAL_CONTRACT["proposal_context_modes"]:
            errors.append("ProposalReview has an unknown or missing context_mode")
        as_is_snapshot_id = str(review.get("as_is_snapshot_id", "")).strip()
        as_is_scope = str(review.get("as_is_applicability_scope", "")).strip()
        if context_mode in {"as_is_and_baseline", "as_is_only"}:
            if not as_is_snapshot_id or not as_is_scope:
                errors.append("as-is context requires as_is_snapshot_id and as_is_applicability_scope")
        elif as_is_snapshot_id or as_is_scope:
            errors.append("context without as-is must not claim an as-is snapshot or scope")
        if context_mode in {"as_is_and_baseline", "baseline_only"}:
            if review.get("baseline_assessment_mode") != "accepted_baseline":
                errors.append("baseline context requires accepted_baseline mode")
        elif review.get("baseline_assessment_mode") == "accepted_baseline":
            errors.append("accepted_baseline mode requires a baseline context mode")
        context_limitations = strings(review.get("context_limitations", []), "context_limitations")
        if context_mode == "documents_only" and not context_limitations:
            errors.append("documents_only context must state its limitations")
        target_entity_ids = strings(review.get("target_entity_ids", []), "target_entity_ids")
        if any(identifier not in registered_ids for identifier in target_entity_ids):
            errors.append("target_entity_ids contains an unknown registered entity")

        baseline_scope = objects("baseline_scope_classifications")
        for item in baseline_scope:
            requirement_id = str(item.get("requirement_id", "")).strip()
            baseline_scope_requirement_ids.append(requirement_id)
            if not requirement_id or requirement_id not in registered_ids:
                errors.append("baseline scope classification refers to an unknown requirement")
            if item.get("status") not in PROPOSAL_CONTRACT["baseline_scope_statuses"]:
                errors.append(f"baseline scope classification {requirement_id or 'without ID'} has an unknown status")
            if not str(item.get("rationale", "")).strip():
                errors.append(f"baseline scope classification {requirement_id or 'without ID'} lacks rationale")
            validate_sources(item.get("source_ids", []), f"baseline scope classification {requirement_id or 'without ID'}")
        if len(baseline_scope_requirement_ids) != len(set(baseline_scope_requirement_ids)) or any(
            not value for value in baseline_scope_requirement_ids
        ):
            errors.append("baseline_scope_classifications require unique non-empty requirement IDs")

        as_is_matches = objects("as_is_fact_matches")
        for item in as_is_matches:
            fact_id = str(item.get("fact_id", "")).strip()
            as_is_match_fact_ids.append(fact_id)
            if not fact_id or fact_id not in registered_ids:
                errors.append("as-is fact match refers to an unknown fact")
            if item.get("status") not in PROPOSAL_CONTRACT["as_is_match_statuses"]:
                errors.append(f"as-is fact match {fact_id or 'without ID'} has an unknown status")
            strings(item.get("quote_item_ids", []), f"as-is fact match {fact_id} quote_item_ids")
            strings(item.get("alternative_ids", []), f"as-is fact match {fact_id} alternative_ids")
            if not str(item.get("notes", "")).strip():
                errors.append(f"as-is fact match {fact_id or 'without ID'} lacks notes")
            validate_sources(item.get("source_ids", []), f"as-is fact match {fact_id or 'without ID'}")
            if current_contract:
                verification_status = str(item.get("verification_status", "")).strip()
                if verification_status not in verification_statuses:
                    errors.append(
                        f"as-is fact match {fact_id or 'without ID'} has an unknown verification_status"
                    )
                elif fact_verification_statuses is not None and verification_status != fact_verification_statuses.get(
                    fact_id
                ):
                    errors.append(
                        f"as-is fact match {fact_id or 'without ID'} does not preserve the source verification_status"
                    )
                if item.get("decision_treatment") not in PROPOSAL_CONTRACT["fact_decision_treatments"]:
                    errors.append(
                        f"as-is fact match {fact_id or 'without ID'} has an unknown decision_treatment"
                    )
                if not str(item.get("decision_impact", "")).strip():
                    errors.append(f"as-is fact match {fact_id or 'without ID'} lacks decision_impact")
                if verification_status in {"conflicted", "requires_confirmation"}:
                    for field in ("confirmation_action", "if_confirmed", "if_refuted"):
                        if not str(item.get(field, "")).strip():
                            errors.append(
                                f"as-is fact match {fact_id or 'without ID'} lacks {field} for an uncertain fact"
                            )
        if len(as_is_match_fact_ids) != len(set(as_is_match_fact_ids)) or any(not value for value in as_is_match_fact_ids):
            errors.append("as_is_fact_matches require unique non-empty fact IDs")

        context_conflicts = objects("context_conflicts")
        for item in context_conflicts:
            conflict_id = str(item.get("conflict_id", "")).strip()
            context_conflict_ids.append(conflict_id)
            status = item.get("status")
            if status not in PROPOSAL_CONTRACT["context_conflict_statuses"]:
                errors.append(f"context conflict {conflict_id or 'without ID'} has an unknown status")
            for field in ("statement", "impact", "resolution"):
                if not str(item.get(field, "")).strip():
                    errors.append(f"context conflict {conflict_id or 'without ID'} lacks {field}")
            strings(item.get("as_is_fact_ids", []), f"context conflict {conflict_id} as_is_fact_ids")
            strings(item.get("baseline_requirement_ids", []), f"context conflict {conflict_id} baseline_requirement_ids")
            strings(item.get("quote_item_ids", []), f"context conflict {conflict_id} quote_item_ids")
            if not isinstance(item.get("blocks_contract"), bool):
                errors.append(f"context conflict {conflict_id or 'without ID'} requires blocks_contract")
            elif status == "open" and item.get("blocks_contract") is True:
                context_contract_blocked = True
            validate_sources(item.get("source_ids", []), f"context conflict {conflict_id or 'without ID'}")
        if len(context_conflict_ids) != len(set(context_conflict_ids)) or any(not value for value in context_conflict_ids):
            errors.append("context_conflicts require unique non-empty conflict IDs")

        criteria = objects("decision_criteria")
        for item in criteria:
            criterion_id = str(item.get("criterion_id", "")).strip()
            decision_criterion_ids.append(criterion_id)
            if not criterion_id or not str(item.get("title", "")).strip() or not str(item.get("rationale", "")).strip():
                errors.append("decision criterion requires criterion_id, title and rationale")
            if item.get("kind") not in PROPOSAL_CONTRACT["decision_criterion_kinds"]:
                errors.append(f"decision criterion {criterion_id or 'without ID'} has an unknown kind")
            weight = item.get("weight")
            if weight is not None and (isinstance(weight, bool) or not isinstance(weight, (int, float)) or weight < 0):
                errors.append(f"decision criterion {criterion_id or 'without ID'} has an invalid weight")
            validate_sources(item.get("source_ids", []), f"decision criterion {criterion_id or 'without ID'}")
        if not criteria or len(decision_criterion_ids) != len(set(decision_criterion_ids)) or any(
            not value for value in decision_criterion_ids
        ):
            errors.append("decision_criteria require unique non-empty criteria")

        comparison_axes = [value["axis_id"] for value in PROPOSAL_CONTRACT["alternative_comparison_axes"]]
        alternative_comparisons = objects("alternative_comparisons")
        for item in alternative_comparisons:
            comparison_id = str(item.get("comparison_id", "")).strip()
            alternative_id = str(item.get("alternative_id", "")).strip()
            alternative_comparison_ids.append(comparison_id)
            if not comparison_id or alternative_id not in known_alternatives:
                errors.append("alternative comparison requires a known alternative and comparison_id")
            results = item.get("axis_results")
            if not isinstance(results, list) or any(not isinstance(value, dict) for value in results):
                errors.append(f"alternative comparison {comparison_id or 'without ID'} axis_results must be objects")
                results = []
            result_axes: list[str] = []
            for result in results:
                axis_id = str(result.get("axis_id", "")).strip()
                result_axes.append(axis_id)
                if result.get("status") not in PROPOSAL_CONTRACT["alternative_comparison_statuses"]:
                    errors.append(f"alternative comparison {comparison_id} axis {axis_id} has an unknown status")
                if not str(result.get("result", "")).strip():
                    errors.append(f"alternative comparison {comparison_id} axis {axis_id} lacks result")
                strings(result.get("blocking_inputs", []), f"alternative comparison {comparison_id} axis {axis_id} blocking_inputs")
                validate_sources(result.get("source_ids", []), f"alternative comparison {comparison_id} axis {axis_id}")
            if sorted(result_axes) != sorted(comparison_axes) or len(result_axes) != len(set(result_axes)):
                errors.append(f"alternative comparison {comparison_id or 'without ID'} does not cover every axis exactly once")
        if not alternative_comparisons or len(alternative_comparison_ids) != len(set(alternative_comparison_ids)) or any(
            not value for value in alternative_comparison_ids
        ):
            errors.append("alternative_comparisons require unique non-empty comparison IDs")

        price_comparisons = objects("price_comparisons")
        for item in price_comparisons:
            comparison_id = str(item.get("comparison_id", "")).strip()
            price_comparison_ids.append(comparison_id)
            if not comparison_id or not str(item.get("subject_id", "")).strip():
                errors.append("price comparison requires comparison_id and subject_id")
            if item.get("status") not in PROPOSAL_CONTRACT["price_comparison_statuses"]:
                errors.append(f"price comparison {comparison_id or 'without ID'} has an unknown status")
            for field in (
                "scope_basis", "quantity_basis", "tax_context", "delivery_context", "installation_context",
                "observed_at", "region",
            ):
                if not str(item.get(field, "")).strip():
                    errors.append(f"price comparison {comparison_id or 'without ID'} lacks {field}")
            price_ids = strings(item.get("price_observation_ids", []), f"price comparison {comparison_id} price_observation_ids")
            if any(identifier not in registered_ids for identifier in price_ids):
                errors.append(f"price comparison {comparison_id or 'without ID'} refers to an unknown PriceObservation")
            limitations = strings(item.get("limitations", []), f"price comparison {comparison_id} limitations")
            if item.get("status") in {"comparable", "partially_comparable"} and not price_ids:
                errors.append(f"price comparison {comparison_id or 'without ID'} has no PriceObservation")
            if item.get("status") in {"not_comparable", "unknown"} and not limitations:
                errors.append(f"price comparison {comparison_id or 'without ID'} must explain its limitations")
            validate_sources(item.get("source_ids", []), f"price comparison {comparison_id or 'without ID'}")
        if not price_comparisons or len(price_comparison_ids) != len(set(price_comparison_ids)) or any(
            not value for value in price_comparison_ids
        ):
            errors.append("price_comparisons require unique non-empty comparison IDs")

        clarifications = objects("clarification_requests")
        mapped_gap_ids: list[str] = []
        rendered_contractor_questions: list[str] = []
        for item in clarifications:
            clarification_id = str(item.get("clarification_id", "")).strip()
            clarification_ids.append(clarification_id)
            gap_id = str(item.get("information_gap_id", "")).strip()
            mapped_gap_ids.append(gap_id)
            recipient = item.get("recipient")
            priority = item.get("priority")
            status = item.get("status")
            if not clarification_id or gap_id not in registered_ids:
                errors.append("clarification request requires clarification_id and a known InformationGap")
            if recipient not in PROPOSAL_CONTRACT["clarification_recipients"]:
                errors.append(f"clarification request {clarification_id or 'without ID'} has an unknown recipient")
            if priority not in PROPOSAL_CONTRACT["clarification_priorities"]:
                errors.append(f"clarification request {clarification_id or 'without ID'} has an unknown priority")
            if status not in PROPOSAL_CONTRACT["clarification_statuses"]:
                errors.append(f"clarification request {clarification_id or 'without ID'} has an unknown status")
            for field in ("question", "requested_evidence", "answer_format"):
                if not str(item.get(field, "")).strip():
                    errors.append(f"clarification request {clarification_id or 'without ID'} lacks {field}")
            blocked = strings(
                item.get("blocked_conclusions", []),
                f"clarification request {clarification_id} blocked_conclusions",
                allow_empty=False,
            )
            response_sources = strings(
                item.get("response_source_ids", []),
                f"clarification request {clarification_id} response_source_ids",
            )
            if any(identifier not in registered_ids for identifier in response_sources):
                errors.append(f"clarification request {clarification_id or 'without ID'} has an unknown response source")
            if status in {"answered", "verified"} and not response_sources:
                errors.append(f"clarification request {clarification_id or 'without ID'} is answered without a response source")
            if status in {"verified", "closed_not_resolved"} and not str(item.get("resolution", "")).strip():
                errors.append(f"clarification request {clarification_id or 'without ID'} lacks resolution")
            if status in {"open", "answered"} and priority in {"critical", "high"} and blocked:
                clarification_contract_blocked = True
            question = str(item.get("question", "")).strip()
            if recipient in {"contractor", "supplier"} and question:
                rendered_contractor_questions.append(question)
            validate_sources(item.get("source_ids", []), f"clarification request {clarification_id or 'without ID'}")
        linked_gap_ids = strings(review.get("information_gap_ids", []), "information_gap_ids")
        if sorted(mapped_gap_ids) != sorted(linked_gap_ids) or len(mapped_gap_ids) != len(set(mapped_gap_ids)):
            errors.append("clarification_requests must cover every linked InformationGap exactly once")
        legacy_questions = strings(review.get("contractor_questions", []), "contractor_questions")
        if sorted(legacy_questions) != sorted(rendered_contractor_questions):
            errors.append("contractor_questions must be derived from contractor and supplier clarification requests")
        if len(clarification_ids) != len(set(clarification_ids)) or any(not value for value in clarification_ids):
            errors.append("clarification_requests require unique non-empty IDs")

        coordination_run_ids = strings(review.get("coordination_run_ids", []), "coordination_run_ids")
        if any(identifier not in registered_ids for identifier in coordination_run_ids):
            errors.append("coordination_run_ids contains an unknown CoordinationRun")

        management_scenarios = objects("management_scenarios")
        for item in management_scenarios:
            scenario_id = str(item.get("scenario_id", "")).strip()
            alternative_id = str(item.get("alternative_id", "")).strip()
            management_scenario_ids.append(scenario_id)
            status = item.get("status")
            if not scenario_id or alternative_id not in known_alternatives:
                errors.append("management scenario requires scenario_id and a known Alternative")
            if status not in PROPOSAL_CONTRACT["management_scenario_statuses"]:
                errors.append(f"management scenario {scenario_id or 'without ID'} has an unknown status")
            for field in ("cost_summary", "schedule_summary"):
                if not str(item.get(field, "")).strip():
                    errors.append(f"management scenario {scenario_id or 'without ID'} lacks {field}")
            blockers = strings(item.get("blocking_inputs", []), f"management scenario {scenario_id} blocking_inputs")
            if status == "blocked" and not blockers:
                errors.append(f"management scenario {scenario_id or 'without ID'} is blocked without blocking_inputs")
            cost_plan_id = str(item.get("cost_plan_id", "")).strip()
            schedule_plan_id = str(item.get("schedule_plan_id", "")).strip()
            if status == "complete" and (not cost_plan_id or not schedule_plan_id):
                errors.append(f"management scenario {scenario_id or 'without ID'} complete without CostPlan and SchedulePlan")
            if alternative_id == str(review.get("foreman_assessment", {}).get("preferred_alternative_id", "")).strip():
                management_contract_blocked = status != "complete"
            for field in ("cost_plan_id", "schedule_plan_id", "change_impact_assessment_id"):
                linked_id = str(item.get(field, "")).strip()
                if linked_id and linked_id not in registered_ids:
                    errors.append(f"management scenario {scenario_id or 'without ID'} has an unknown {field}")
            validate_sources(item.get("source_ids", []), f"management scenario {scenario_id or 'without ID'}")
        if not management_scenarios or len(management_scenario_ids) != len(set(management_scenario_ids)) or any(
            not value for value in management_scenario_ids
        ):
            errors.append("management_scenarios require unique non-empty scenario IDs")

        challenge = review.get("challenge_review")
        if not isinstance(challenge, dict):
            errors.append("challenge_review must be an object")
        else:
            for field in ("recommendation_under_test", "strongest_counterargument", "conclusion"):
                if not str(challenge.get(field, "")).strip():
                    errors.append(f"challenge_review lacks {field}")
            if challenge.get("status") not in PROPOSAL_CONTRACT["challenge_review_statuses"]:
                errors.append("challenge_review has an unknown status")
            strings(challenge.get("failure_modes", []), "challenge_review.failure_modes", allow_empty=False)
            strings(challenge.get("decision_changing_inputs", []), "challenge_review.decision_changing_inputs", allow_empty=False)
            validate_sources(challenge.get("source_ids", []), "challenge_review")
            challenge_completed = True

    if not isinstance(manifest, dict):
        errors.append("completion_manifest must be an object")
    else:
        manifest_fields = [
            ("mandatory_check_ids", mandatory_ids),
            ("discipline_check_keys", discipline_keys),
            ("technical_alternative_track_ids", track_ids),
            ("additional_model_check_ids", additional_ids),
        ]
        if current_contract:
            manifest_fields.extend([
                ("scope_ids", scope_ids),
                ("constructability_phase_ids", phase_ids),
                ("contractor_assessment_axis_ids", contractor_axis_ids),
                ("site_verification_ids", site_verification_ids),
                ("acceptance_plan_ids", acceptance_plan_ids),
                ("priority_risk_ids", priority_risk_ids),
                ("baseline_scope_requirement_ids", baseline_scope_requirement_ids),
                ("as_is_match_fact_ids", as_is_match_fact_ids),
                ("context_conflict_ids", context_conflict_ids),
                ("decision_criterion_ids", decision_criterion_ids),
                ("alternative_comparison_ids", alternative_comparison_ids),
                ("price_comparison_ids", price_comparison_ids),
                ("clarification_ids", clarification_ids),
                ("coordination_run_ids", coordination_run_ids),
                ("management_scenario_ids", management_scenario_ids),
            ])
            if manifest.get("challenge_review_completed") is not challenge_completed:
                errors.append("completion_manifest.challenge_review_completed does not match challenge_review")
        for field, expected in manifest_fields:
            value = manifest.get(field)
            if (
                not isinstance(value, list)
                or any(not isinstance(item, str) or not item.strip() for item in value)
                or sorted(value) != sorted(expected)
                or len(value) != len(set(value))
            ):
                errors.append(f"completion_manifest.{field} does not match the recorded checks")

    if review.get("status") == "ready_for_owner" and any(status not in ready_statuses for status in required_statuses):
        errors.append("ready review has blocked or specialist-required contract items")
    if current_contract and review.get("status") == "ready_for_owner":
        foreman = review.get("foreman_assessment", {})
        readiness = foreman.get("decision_readiness") if isinstance(foreman, dict) else None
        if readiness == "preliminary":
            errors.append("ready review cannot have preliminary decision_readiness")
        if readiness == "ready_for_contract":
            if review.get("baseline_assessment_mode") != "accepted_baseline":
                errors.append("ready_for_contract requires an accepted baseline snapshot")
            if review.get("context_mode") == "documents_only":
                errors.append("ready_for_contract requires an as-is or baseline context")
            if foreman.get("verdict") != "conditionally_recommended":
                errors.append("ready_for_contract requires a conditionally_recommended foreman verdict")
            if cost_contract_blocked:
                errors.append("ready_for_contract has unresolved cost exposure")
            if any(status not in {"completed", "not_applicable"} for status in site_statuses):
                errors.append("ready_for_contract has open site verification")
            if any(status not in ready_statuses for status in contractor_statuses):
                errors.append("ready_for_contract has incomplete contractor assessment")
            if context_contract_blocked:
                errors.append("ready_for_contract has an unresolved blocking context conflict")
            if clarification_contract_blocked:
                errors.append("ready_for_contract has an open critical or high-priority clarification")
            if management_contract_blocked:
                errors.append("ready_for_contract has a blocked preferred management scenario")
        if isinstance(review.get("challenge_review"), dict) and review["challenge_review"].get("status") == "insufficient_evidence":
            errors.append("ready review has insufficient evidence after the challenge pass")
    elif current_contract:
        foreman = review.get("foreman_assessment", {})
        if isinstance(foreman, dict) and foreman.get("decision_readiness") in {
            "ready_for_negotiation",
            "ready_for_contract",
        }:
            errors.append("a non-ready review must keep preliminary decision_readiness")
    return errors


def normalized_unit_set(value: object) -> set[str] | None:
    if not isinstance(value, list) or not value:
        return None
    normalized: list[str] = []
    for unit in value:
        if isinstance(unit, bool) or not isinstance(unit, (int, str)):
            return None
        if isinstance(unit, str) and not unit.strip():
            return None
        normalized.append(json.dumps(unit, ensure_ascii=False, sort_keys=True))
    if len(normalized) != len(set(normalized)):
        return None
    return set(normalized)


def validate_baseline_snapshots(
    jsonl_records: dict[str, list[tuple[int, dict]]],
    jsonl_ids: dict[str, set[str]],
    document_versions: dict[str, set[tuple[object, str]]],
    complete_read_versions: set[tuple[str, object, str]],
    proposal_document_ids: set[str],
    warnings: list[str],
) -> set[str]:
    snapshots = records_by_id(jsonl_records["baseline_snapshots.jsonl"], "baseline_snapshot_id")
    requirements = records_by_id(jsonl_records["approved_requirements.jsonl"], "requirement_id")
    facts = records_by_id(jsonl_records["facts.jsonl"], "fact_id")
    decisions = records_by_id(jsonl_records["decisions.jsonl"], "decision_id")
    quote_source_ids = proposal_document_ids | {
        str(record.get("source_document_id", "")).strip()
        for _, record in jsonl_records["quotes.jsonl"]
        if str(record.get("source_document_id", "")).strip()
    }
    seen_versions: dict[int, str] = {}
    superseded_ids: set[str] = set()

    for line_number, snapshot in jsonl_records["baseline_snapshots.jsonl"]:
        location = f"baseline_snapshots.jsonl:{line_number}"
        snapshot_id = str(snapshot.get("baseline_snapshot_id", "")).strip()
        baseline_version = snapshot.get("baseline_version")
        if not isinstance(baseline_version, int) or isinstance(baseline_version, bool) or baseline_version < 1:
            warnings.append(f"{location}: baseline_version must be a positive integer")
        elif baseline_version in seen_versions:
            warnings.append(f"{location}: duplicate baseline_version {baseline_version}")
        else:
            seen_versions[baseline_version] = snapshot_id
        if not str(snapshot.get("scope", "")).strip():
            warnings.append(f"{location}: scope is required")
        if not str(snapshot.get("accepted_at", "")).strip():
            warnings.append(f"{location}: accepted_at is required")

        decision_id = str(snapshot.get("owner_decision_id", "")).strip()
        decision = decisions.get(decision_id)
        if (
            decision is None
            or decision.get("decision_type") != "baseline_acceptance"
            or decision.get("status") != "approved"
            or decision.get("approved_by") != "owner"
            or not str(decision.get("approved_at", "")).strip()
        ):
            warnings.append(f"{location}: no explicit approved owner baseline decision")
        elif snapshot.get("accepted_at") != decision.get("approved_at"):
            warnings.append(f"{location}: accepted_at must match the owner decision approved_at")

        supersedes = str(snapshot.get("supersedes_baseline_snapshot_id", "")).strip()
        if isinstance(baseline_version, int) and baseline_version == 1 and supersedes:
            warnings.append(f"{location}: baseline version 1 must not supersede another snapshot")
        if isinstance(baseline_version, int) and baseline_version > 1:
            prior = snapshots.get(supersedes)
            if prior is None or prior.get("baseline_version") != baseline_version - 1:
                warnings.append(f"{location}: versioned baseline must supersede the immediately preceding snapshot")
            else:
                superseded_ids.add(supersedes)

        snapshot_requirement_ids = id_list(snapshot, "requirement_ids", location, warnings)
        if not snapshot_requirement_ids:
            warnings.append(f"{location}: requirement_ids must not be empty")
        if len(snapshot_requirement_ids) != len(set(snapshot_requirement_ids)):
            warnings.append(f"{location}: requirement_ids contains duplicates")
        if any(value not in requirements for value in snapshot_requirement_ids):
            warnings.append(f"{location}: unknown requirement_id")

        owner_requirement_ids = id_list(snapshot, "owner_requirement_ids", location, warnings)
        document_entries = snapshot.get("document_versions")
        if not isinstance(document_entries, list) or not document_entries or any(
            not isinstance(value, dict) for value in document_entries
        ):
            warnings.append(f"{location}: document_versions must be a non-empty array of objects")
            document_entries = []
        contributed_requirement_ids: list[str] = []
        seen_document_versions: set[tuple[str, object, str]] = set()
        for number, entry in enumerate(document_entries, 1):
            entry_location = f"{location}:document {number}"
            document_id = str(entry.get("document_id", "")).strip()
            version = entry.get("document_version")
            sha256 = str(entry.get("sha256", "")).strip()
            version_key = (document_id, version, sha256)
            if not document_id or not isinstance(version, int) or isinstance(version, bool) or not sha256:
                warnings.append(f"{entry_location}: exact document identity is required")
            elif (version, sha256) not in document_versions.get(document_id, set()):
                warnings.append(f"{entry_location}: document version and SHA-256 are not indexed")
            elif version_key not in complete_read_versions:
                warnings.append(f"{entry_location}: selected document version has no complete ReadingRun")
            if version_key in seen_document_versions:
                warnings.append(f"{entry_location}: duplicate selected document version")
            seen_document_versions.add(version_key)
            if document_id in quote_source_ids:
                warnings.append(f"{entry_location}: a quote source cannot be part of the baseline")
            for field in ("project_role", "applicability_scope"):
                if not str(entry.get(field, "")).strip():
                    warnings.append(f"{entry_location}: {field} is required")
            for field in ("technical_approval_status", "official_approval_status"):
                if entry.get(field) not in DIMENSIONS["document_approval_status"]:
                    warnings.append(f"{entry_location}: unknown {field}")
            entry_requirement_ids = id_list(entry, "requirement_ids", entry_location, warnings)
            if not entry_requirement_ids:
                warnings.append(f"{entry_location}: selected document contributes no project requirement")
            contributed_requirement_ids.extend(entry_requirement_ids)
            for requirement_id in entry_requirement_ids:
                requirement = requirements.get(requirement_id)
                if requirement is None:
                    continue
                matching_fact = False
                for fact_id in id_list(requirement, "source_fact_ids", entry_location, warnings):
                    fact = facts.get(fact_id)
                    if (
                        fact is not None
                        and fact.get("source_document_id") == document_id
                        and fact.get("document_version") == version
                        and str(fact.get("sha256", "")).strip() == sha256
                        and str(fact.get("locator", "")).strip()
                    ):
                        matching_fact = True
                if not matching_fact:
                    warnings.append(f"{entry_location}: requirement {requirement_id} has no precise locator in this document version")

        expected_membership = set(contributed_requirement_ids) | set(owner_requirement_ids)
        if expected_membership != set(snapshot_requirement_ids):
            warnings.append(f"{location}: snapshot requirement_ids do not match document and owner requirement membership")

        conflicts = snapshot.get("conflict_resolutions", [])
        if not isinstance(conflicts, list) or any(not isinstance(value, dict) for value in conflicts):
            warnings.append(f"{location}: conflict_resolutions must be an array of objects")
        else:
            seen_conflicts: set[str] = set()
            for number, conflict in enumerate(conflicts, 1):
                conflict_id = str(conflict.get("conflict_id", "")).strip()
                if not conflict_id or conflict_id in seen_conflicts:
                    warnings.append(f"{location}: conflict {number} has a missing or duplicate conflict_id")
                seen_conflicts.add(conflict_id)
                if not str(conflict.get("statement", "")).strip() or not str(conflict.get("resolution", "")).strip():
                    warnings.append(f"{location}: conflict {number} requires statement and owner-accepted resolution")
                source_fact_ids = id_list(conflict, "source_fact_ids", f"{location}:conflict {number}", warnings)
                if not source_fact_ids or any(value not in facts for value in source_fact_ids):
                    warnings.append(f"{location}: conflict {number} has missing or unknown source facts")

        for requirement_id in snapshot_requirement_ids:
            requirement = requirements.get(requirement_id)
            if requirement is None:
                continue
            if requirement.get("baseline_snapshot_id") != snapshot_id:
                warnings.append(f"{location}: requirement {requirement_id} is not linked back to this snapshot")
            if requirement.get("decision_id") != decision_id:
                warnings.append(f"{location}: requirement {requirement_id} is not linked to the baseline owner decision")
            if requirement.get("baseline_status") != "approved":
                warnings.append(f"{location}: requirement {requirement_id} is not owner-approved")
            if requirement.get("verification_status") != "verified":
                warnings.append(f"{location}: requirement {requirement_id} is not verified")

    current_ids = set(snapshots) - superseded_ids
    if len(current_ids) > 1:
        warnings.append("baseline_snapshots.jsonl: more than one current baseline snapshot")
    return current_ids


def validate_proposal_reviews(
    root: Path,
    jsonl_records: dict[str, list[tuple[int, dict]]],
    jsonl_ids: dict[str, set[str]],
    active_documents: set[str],
    current_versions: dict[str, tuple[object, str]],
    document_versions: dict[str, set[tuple[object, str]]],
    complete_read_versions: set[tuple[str, object, str]],
    warnings: list[str],
) -> None:
    inventories = records_by_id(jsonl_records["document_inventories.jsonl"], "inventory_id")
    reading_runs = records_by_id(jsonl_records["reading_runs.jsonl"], "reading_run_id")
    extraction_runs = records_by_id(jsonl_records["fact_extraction_runs.jsonl"], "extraction_run_id")
    facts = records_by_id(jsonl_records["facts.jsonl"], "fact_id")
    quotes = records_by_id(jsonl_records["quotes.jsonl"], "quote_id")
    quote_items = records_by_id(jsonl_records["quote_items.jsonl"], "quote_item_id")
    baseline_snapshots = records_by_id(jsonl_records["baseline_snapshots.jsonl"], "baseline_snapshot_id")
    as_is_snapshots = records_by_id(jsonl_records["as_is_snapshots.jsonl"], "as_is_snapshot_id")
    proposal_reviews = records_by_id(jsonl_records["proposal_reviews.jsonl"], "proposal_review_id")
    coordination_runs = records_by_id(jsonl_records["coordination_runs.jsonl"], "coordination_run_id")
    price_observations = records_by_id(jsonl_records["price_observations.jsonl"], "price_observation_id")
    cost_plans = records_by_id(jsonl_records["cost_plans.jsonl"], "cost_plan_id")
    schedule_plans = records_by_id(jsonl_records["schedule_plans.jsonl"], "schedule_plan_id")
    change_impacts = records_by_id(
        jsonl_records["change_impact_assessments.jsonl"], "change_impact_assessment_id"
    )
    superseded_as_is_ids = {
        str(value.get("supersedes_as_is_snapshot_id", "")).strip()
        for value in as_is_snapshots.values()
        if str(value.get("supersedes_as_is_snapshot_id", "")).strip()
    }
    current_as_is_ids = set(as_is_snapshots) - superseded_as_is_ids
    compliance_assessments = records_by_id(
        jsonl_records["compliance_assessments.jsonl"], "compliance_assessment_id"
    )
    registered_ids = set(active_documents)
    for values in jsonl_ids.values():
        registered_ids.update(values)

    for line_number, review in jsonl_records["proposal_reviews.jsonl"]:
        location = f"proposal_reviews.jsonl:{line_number}"
        manifest = review.get("completion_manifest")
        current_contract = isinstance(manifest, dict) and manifest.get("contract_version") == PROPOSAL_CONTRACT["contract_version"]
        status = review.get("status")
        if status not in DIMENSIONS["proposal_review_status"]:
            warnings.append(f"{location}: unknown proposal review status")
        source_id = str(review.get("source_document_id", "")).strip()
        version_key = (review.get("document_version"), str(review.get("sha256", "")).strip())
        if source_id not in active_documents:
            warnings.append(f"{location}: source document is not active")
        elif current_versions.get(source_id) != version_key:
            warnings.append(f"{location}: source version is not current")
        disciplines = review.get("disciplines")
        if not isinstance(disciplines, list) or not disciplines or any(
            not isinstance(value, str) or not value.strip() for value in disciplines
        ):
            warnings.append(f"{location}: disciplines must be a non-empty string array")

        quote_id = str(review.get("quote_id", "")).strip()
        quote = quotes.get(quote_id)
        if (
            quote is None
            or quote.get("source_document_id") != source_id
            or (quote.get("document_version"), str(quote.get("sha256", "")).strip()) != version_key
        ):
            warnings.append(f"{location}: quote is missing or belongs to another source version")
        inventory_id = str(review.get("inventory_id", "")).strip()
        inventory = inventories.get(inventory_id)
        if (
            inventory is None
            or inventory.get("source_document_id") != source_id
            or (inventory.get("document_version"), str(inventory.get("sha256", "")).strip()) != version_key
        ):
            warnings.append(f"{location}: inventory is missing or does not match the source version")

        run_ids = id_list(review, "reading_run_ids", location, warnings)
        complete_run = False
        for run_id in run_ids:
            run = reading_runs.get(run_id)
            if run is None:
                warnings.append(f"{location}: unknown reading_run_id {run_id}")
            elif inventory is not None:
                coverage = run.get("coverage")
                expected = normalized_unit_set(coverage.get("expected_units")) if isinstance(coverage, dict) else None
                checked = normalized_unit_set(coverage.get("checked_units")) if isinstance(coverage, dict) else None
                inventoried = normalized_unit_set(inventory.get("expected_units"))
                requirements = inventory.get("reading_requirements", [])
                checked_requirements = coverage.get("checked_requirements", []) if isinstance(coverage, dict) else None
                summary_path = str(run.get("summary_path", "")).strip()
                summary = root / summary_path if summary_path else None
                summaries_root = (root / ".home-control" / "summaries").resolve()
                try:
                    summary_resolved = summary.resolve() if summary else None
                    summary_valid = bool(
                        summary_resolved
                        and summaries_root in summary_resolved.parents
                        and summary_resolved.is_file()
                        and summary is not None
                        and not is_linklike(summary)
                    )
                except (OSError, RuntimeError):
                    summary_valid = False
                if (
                    run.get("source_document_id") == source_id
                    and (run.get("document_version"), str(run.get("sha256", "")).strip()) == version_key
                    and run.get("status") == "complete"
                    and inventory.get("status") == "complete"
                    and expected is not None
                    and expected == checked == inventoried
                    and isinstance(coverage, dict)
                    and coverage.get("gaps") == []
                    and isinstance(requirements, list)
                    and isinstance(checked_requirements, list)
                    and all(isinstance(value, str) and value.strip() for value in requirements)
                    and all(isinstance(value, str) and value.strip() for value in checked_requirements)
                    and len(checked_requirements) == len(set(checked_requirements))
                    and set(checked_requirements) == set(requirements)
                    and summary_valid
                ):
                    complete_run = True

        extraction_ids = id_list(review, "fact_extraction_run_ids", location, warnings)
        complete_extraction = False
        for extraction_id in extraction_ids:
            extraction = extraction_runs.get(extraction_id)
            if extraction is None:
                warnings.append(f"{location}: unknown fact extraction run {extraction_id}")
                continue
            expected_sections = normalized_string_set(extraction.get("expected_sections"))
            checked_sections = normalized_string_set(extraction.get("checked_sections"))
            if (
                extraction.get("source_document_id") == source_id
                and (extraction.get("document_version"), str(extraction.get("sha256", "")).strip()) == version_key
                and extraction.get("status") == "complete"
                and expected_sections
                and checked_sections == expected_sections
                and extraction.get("coverage_gaps") == []
            ):
                complete_extraction = True
        package_ids = id_list(review, "project_package_ids", location, warnings)
        if current_contract and (
            not package_ids or any(value not in jsonl_ids["project_packages.jsonl"] for value in package_ids)
        ):
            warnings.append(f"{location}: current review needs at least one known project package")
        for field, registry in (
            ("information_gap_ids", "information_gaps.jsonl"),
            ("coordination_issue_ids", "coordination_issues.jsonl"),
        ):
            linked = id_list(review, field, location, warnings)
            if any(value not in jsonl_ids[registry] for value in linked):
                warnings.append(f"{location}: {field} contains an unknown link")
        if current_contract and not isinstance(review.get("compliance_assessment_ids"), list):
            warnings.append(f"{location}: compliance_assessment_ids must be an array")
        compliance_ids = id_list(review, "compliance_assessment_ids", location, warnings)
        if any(value not in compliance_assessments for value in compliance_ids):
            warnings.append(f"{location}: compliance_assessment_ids contains an unknown link")
        mandatory_checks = review.get("mandatory_checks", [])
        normative_check = next(
            (
                value
                for value in mandatory_checks
                if isinstance(value, dict) and value.get("check_id") == "norms_and_specialist_boundary"
            ),
            None,
        )
        if current_contract and isinstance(normative_check, dict) and normative_check.get("status") == "completed":
            if not compliance_ids:
                warnings.append(f"{location}: completed normative check needs a ComplianceAssessment")
            elif any(compliance_assessments[value].get("status") != "complete" for value in compliance_ids if value in compliance_assessments):
                warnings.append(f"{location}: completed normative check links a non-complete ComplianceAssessment")
            normative_sources = id_list(normative_check, "source_ids", location, warnings)
            if not set(normative_sources) & set(compliance_ids):
                warnings.append(f"{location}: completed normative check does not cite its ComplianceAssessment")

        baseline_mode = review.get("baseline_assessment_mode")
        baseline_snapshot_id = str(review.get("baseline_snapshot_id", "")).strip()
        baseline_applicability_scope = str(review.get("baseline_applicability_scope", "")).strip()
        baseline_snapshot = baseline_snapshots.get(baseline_snapshot_id)
        if current_contract and baseline_mode not in DIMENSIONS["baseline_assessment_mode"]:
            warnings.append(f"{location}: unknown or missing baseline_assessment_mode")
        baseline_ids = id_list(review, "baseline_requirement_ids", location, warnings)
        if any(value not in jsonl_ids["approved_requirements.jsonl"] for value in baseline_ids):
            warnings.append(f"{location}: unknown baseline requirement")
        if current_contract and baseline_mode == "accepted_baseline":
            if baseline_snapshot is None:
                warnings.append(f"{location}: accepted_baseline must use an existing BaselineSnapshot")
            else:
                snapshot_requirement_ids = set(id_list(baseline_snapshot, "requirement_ids", location, warnings))
                if not baseline_applicability_scope:
                    warnings.append(f"{location}: baseline_applicability_scope is required")
                if not baseline_ids or not set(baseline_ids).issubset(snapshot_requirement_ids):
                    warnings.append(f"{location}: baseline requirements must be a non-empty snapshot subset")
        elif current_contract:
            if baseline_snapshot_id or baseline_ids or baseline_applicability_scope:
                warnings.append(f"{location}: reference-only review must not claim an accepted baseline")
        matches = review.get("requirement_matches")
        matched_requirements: list[str] = []
        covered_items: set[str] = set()
        current_quote_items = {
            item_id for item_id, item in quote_items.items() if item.get("quote_id") == quote_id
        }
        if current_contract and baseline_mode != "accepted_baseline":
            for item_id in current_quote_items:
                linked_requirements = quote_items[item_id].get("approved_requirement_ids", [])
                if isinstance(linked_requirements, list) and linked_requirements:
                    warnings.append(f"{location}: reference-only quote item {item_id} links an approved requirement")
        if not isinstance(matches, list) or any(not isinstance(value, dict) for value in matches):
            warnings.append(f"{location}: requirement_matches must be an array of objects")
            matches = []
        for match_number, match in enumerate(matches, 1):
            requirement_id = str(match.get("requirement_id", "")).strip()
            matched_requirements.append(requirement_id)
            if requirement_id not in jsonl_ids["approved_requirements.jsonl"]:
                warnings.append(f"{location}: match {match_number} has an unknown requirement")
            if match.get("status") not in DIMENSIONS["proposal_match_status"]:
                warnings.append(f"{location}: match {match_number} has an unknown status")
            linked = id_list(match, "quote_item_ids", f"{location}:match {match_number}", warnings)
            if any(value not in current_quote_items for value in linked):
                warnings.append(f"{location}: match {match_number} links an item from another quote")
            covered_items.update(linked)
        if sorted(matched_requirements) != sorted(baseline_ids) or len(matched_requirements) != len(set(matched_requirements)):
            warnings.append(f"{location}: every baseline requirement must be covered exactly once")
        unmatched = set(id_list(review, "unmatched_quote_item_ids", location, warnings))
        if unmatched & covered_items or unmatched | covered_items != current_quote_items:
            warnings.append(f"{location}: every quote item must be classified exactly once")

        reference_comparisons = review.get("reference_comparisons", [])
        if not isinstance(reference_comparisons, list) or any(
            not isinstance(value, dict) for value in reference_comparisons
        ):
            warnings.append(f"{location}: reference_comparisons must be an array of objects")
            reference_comparisons = []
        for number, comparison in enumerate(reference_comparisons, 1):
            comparison_location = f"{location}:reference comparison {number}"
            document_id = str(comparison.get("document_id", "")).strip()
            version = comparison.get("document_version")
            sha256 = str(comparison.get("sha256", "")).strip()
            if (version, sha256) not in document_versions.get(document_id, set()):
                warnings.append(f"{comparison_location}: unknown document version")
            elif (document_id, version, sha256) not in complete_read_versions:
                warnings.append(f"{comparison_location}: reference document version has no complete ReadingRun")
            if document_id == source_id:
                warnings.append(f"{comparison_location}: proposal cannot be its own reference document")
            for field in ("project_role", "applicability_scope", "statement", "locator", "limitations"):
                if not str(comparison.get(field, "")).strip():
                    warnings.append(f"{comparison_location}: {field} is required")
            if comparison.get("status") not in DIMENSIONS["proposal_match_status"]:
                warnings.append(f"{comparison_location}: unknown comparison status")
            linked_items = id_list(comparison, "quote_item_ids", comparison_location, warnings)
            if any(value not in current_quote_items for value in linked_items):
                warnings.append(f"{comparison_location}: links an item from another quote")
        baseline_limitations = review.get("baseline_limitations", [])
        if not isinstance(baseline_limitations, list) or any(
            not isinstance(value, str) or not value.strip() for value in baseline_limitations
        ):
            warnings.append(f"{location}: baseline_limitations must be a string array")
            baseline_limitations = []
        if current_contract and baseline_mode == "reference_only" and not reference_comparisons:
            warnings.append(f"{location}: reference_only mode requires at least one explicit reference comparison")
        if current_contract and baseline_mode == "no_relevant_documents" and reference_comparisons:
            warnings.append(f"{location}: no_relevant_documents mode cannot contain reference comparisons")
        if current_contract and baseline_mode != "accepted_baseline" and not baseline_limitations:
            warnings.append(f"{location}: review without an accepted baseline must state dependent limitations")

        checks = review.get("technical_checks")
        if not isinstance(checks, list) or not checks or any(not isinstance(value, dict) for value in checks):
            warnings.append(f"{location}: technical_checks must be a non-empty array of objects")
            checks = []
        seen_checks: set[str] = set()
        for check in checks:
            check_id = str(check.get("check_id", "")).strip()
            if not check_id or check_id in seen_checks:
                warnings.append(f"{location}: missing or duplicate technical check ID")
            seen_checks.add(check_id)
            if not str(check.get("category", "")).strip() or not str(check.get("criterion", "")).strip():
                warnings.append(f"{location}: technical check requires category and criterion")
            if check.get("status") not in DIMENSIONS["technical_check_status"]:
                warnings.append(f"{location}: technical check has unknown status")
            sources = id_list(check, "source_ids", f"{location}:check {check_id}", warnings)
            if any(value not in registered_ids for value in sources):
                warnings.append(f"{location}: technical check has an unknown source")

        calculations = review.get("calculations", [])
        if not isinstance(calculations, list) or any(not isinstance(value, dict) for value in calculations):
            warnings.append(f"{location}: calculations must be an array of objects")
            calculations = []
        for calculation in calculations:
            calc_id = str(calculation.get("calculation_id", "")).strip()
            if not calc_id or not str(calculation.get("formula", "")).strip():
                warnings.append(f"{location}: calculation requires an ID and formula")
            if calculation.get("status") not in DIMENSIONS["calculation_status"]:
                warnings.append(f"{location}: calculation {calc_id or 'without ID'} has unknown status")
            inputs = calculation.get("inputs")
            if not isinstance(inputs, list) or any(not isinstance(value, dict) for value in inputs):
                warnings.append(f"{location}: calculation {calc_id or 'without ID'} has invalid inputs")
                continue
            for value in inputs:
                if not str(value.get("name", "")).strip() or "value" not in value or not str(value.get("unit", "")).strip():
                    warnings.append(f"{location}: calculation {calc_id} has an incomplete input")
                sources = id_list(value, "source_ids", f"{location}:calculation {calc_id}", warnings)
                if any(item not in registered_ids for item in sources):
                    warnings.append(f"{location}: calculation {calc_id} has an unknown source")

        searches = review.get("search_runs")
        if not isinstance(searches, list) or any(not isinstance(value, dict) for value in searches):
            warnings.append(f"{location}: search_runs must be an array of objects")
            searches = []
        comparable_candidate_ids: set[str] = set()
        for search in searches:
            search_id = str(search.get("search_run_id", "")).strip()
            if not search_id or search.get("status") not in DIMENSIONS["search_run_status"]:
                warnings.append(f"{location}: invalid external search run")
            queries = search.get("queries")
            if not isinstance(queries, list) or not queries or any(not isinstance(value, str) or not value.strip() for value in queries):
                warnings.append(f"{location}: search {search_id or 'without ID'} has no reproducible queries")
            urls = search.get("source_urls")
            if not isinstance(urls, list) or any(not isinstance(value, str) or not value.strip() for value in urls):
                warnings.append(f"{location}: search {search_id or 'without ID'} has invalid source URLs")
            if not str(search.get("checked_at", "")).strip() or not str(search.get("region", "")).strip():
                warnings.append(f"{location}: search {search_id or 'without ID'} lacks date or region")
            privacy = search.get("privacy_review")
            if not isinstance(privacy, dict) or privacy.get("unnecessary_private_data_removed") is not True:
                warnings.append(f"{location}: search {search_id or 'without ID'} lacks privacy confirmation")
            candidates = id_list(search, "candidate_contractor_ids", f"{location}:search {search_id}", warnings)
            if any(value not in jsonl_ids["contractors.jsonl"] for value in candidates):
                warnings.append(f"{location}: search has an unknown contractor candidate")
            supplier_candidates = id_list(search, "candidate_supplier_ids", f"{location}:search {search_id}", warnings)
            if any(value not in jsonl_ids["suppliers.jsonl"] for value in supplier_candidates):
                warnings.append(f"{location}: search has an unknown supplier candidate")
            if not current_contract:
                continue
            candidate_assessments = search.get("candidate_assessments")
            if not isinstance(candidate_assessments, list) or any(
                not isinstance(value, dict) for value in candidate_assessments
            ):
                warnings.append(f"{location}: search {search_id or 'without ID'} candidate_assessments must be an array of objects")
                candidate_assessments = []
            assessed_ids: list[str] = []
            for assessment in candidate_assessments:
                counterparty_id = str(assessment.get("counterparty_id", "")).strip()
                counterparty_kind = str(assessment.get("counterparty_kind", "")).strip()
                assessed_ids.append(counterparty_id)
                if counterparty_kind == "contractor":
                    if counterparty_id not in candidates:
                        warnings.append(f"{location}: candidate assessment refers to an unlisted contractor")
                elif counterparty_kind == "supplier":
                    if counterparty_id not in supplier_candidates:
                        warnings.append(f"{location}: candidate assessment refers to an unlisted supplier")
                else:
                    warnings.append(f"{location}: candidate assessment has an unknown counterparty_kind")
                comparability_status = assessment.get("comparability_status")
                if comparability_status not in PROPOSAL_CONTRACT["candidate_comparability_statuses"]:
                    warnings.append(f"{location}: candidate assessment has an unknown comparability_status")
                if not str(assessment.get("basis", "")).strip():
                    warnings.append(f"{location}: candidate assessment lacks a comparability basis")
                missing_information = assessment.get("missing_information")
                if not isinstance(missing_information, list) or any(
                    not isinstance(value, str) or not value.strip() for value in missing_information
                ):
                    warnings.append(f"{location}: candidate assessment has invalid missing_information")
                assessment_urls = assessment.get("source_urls")
                if not isinstance(assessment_urls, list) or not assessment_urls or any(
                    not isinstance(value, str) or not value.strip() for value in assessment_urls
                ):
                    warnings.append(f"{location}: candidate assessment requires direct source URLs")
                if (
                    search.get("status") in {"complete", "partial"}
                    and comparability_status in {"potentially_comparable", "requires_quote"}
                    and counterparty_id
                ):
                    comparable_candidate_ids.add(counterparty_id)
            listed_ids = [*candidates, *supplier_candidates]
            if sorted(assessed_ids) != sorted(listed_ids) or len(assessed_ids) != len(set(assessed_ids)):
                warnings.append(f"{location}: every candidate must have exactly one comparability assessment")

        findings = id_list(review, "finding_ids", location, warnings)
        if any(value not in jsonl_ids["findings.jsonl"] for value in findings):
            warnings.append(f"{location}: unknown finding link")
        alternatives = id_list(review, "alternative_ids", location, warnings)
        if any(value not in jsonl_ids["alternatives.jsonl"] for value in alternatives):
            warnings.append(f"{location}: unknown alternative link")
        if current_contract:
            revision = review.get("review_revision")
            series_id = str(review.get("review_series_id", "")).strip()
            supersedes_review_id = str(review.get("supersedes_proposal_review_id", "")).strip()
            same_series = [
                value for identifier, value in proposal_reviews.items()
                if value.get("review_series_id") == series_id and identifier != review.get("proposal_review_id")
            ]
            if any(value.get("review_revision") == revision for value in same_series):
                warnings.append(f"{location}: duplicate review_revision in review_series_id")
            if isinstance(revision, int) and revision > 1:
                prior = proposal_reviews.get(supersedes_review_id)
                if (
                    prior is None
                    or prior.get("review_series_id") != series_id
                    or prior.get("review_revision") != revision - 1
                ):
                    warnings.append(f"{location}: revision does not supersede its immediate predecessor")

            context_mode = review.get("context_mode")
            as_is_snapshot_id = str(review.get("as_is_snapshot_id", "")).strip()
            as_is_snapshot = as_is_snapshots.get(as_is_snapshot_id)
            as_is_matches = review.get("as_is_fact_matches", [])
            if context_mode in {"as_is_and_baseline", "as_is_only"}:
                if as_is_snapshot is None or as_is_snapshot_id not in current_as_is_ids:
                    warnings.append(f"{location}: as-is context does not use the current AsIsSnapshot")
                else:
                    snapshot_fact_ids = id_list(as_is_snapshot, "source_fact_ids", location, warnings)
                    matched_fact_ids = [
                        str(value.get("fact_id", "")).strip()
                        for value in as_is_matches
                        if isinstance(value, dict)
                    ]
                    if sorted(matched_fact_ids) != sorted(snapshot_fact_ids):
                        warnings.append(f"{location}: every AsIsSnapshot fact must be classified exactly once")
                    for match in as_is_matches if isinstance(as_is_matches, list) else []:
                        if not isinstance(match, dict):
                            continue
                        fact_id = str(match.get("fact_id", "")).strip()
                        source_fact = facts.get(fact_id)
                        if source_fact is not None and match.get("verification_status") != source_fact.get(
                            "verification_status"
                        ):
                            warnings.append(
                                f"{location}: as-is fact match {fact_id} does not preserve the source verification_status"
                            )
                    snapshot_entity_ids: set[str] = set()
                    for field in (
                        "site_ids", "zone_ids", "physical_element_ids", "system_ids", "asset_ids", "route_ids",
                        "asset_event_ids", "condition_assessment_ids",
                    ):
                        snapshot_entity_ids.update(id_list(as_is_snapshot, field, location, warnings))
                    target_ids = set(id_list(review, "target_entity_ids", location, warnings))
                    if target_ids - snapshot_entity_ids:
                        warnings.append(f"{location}: target_entity_ids fall outside the AsIsSnapshot")
                    if snapshot_entity_ids and not target_ids:
                        warnings.append(f"{location}: as-is context does not select target entities")
            elif isinstance(as_is_matches, list) and as_is_matches:
                warnings.append(f"{location}: review without as-is context contains as_is_fact_matches")

            baseline_scope = review.get("baseline_scope_classifications", [])
            if baseline_mode == "accepted_baseline" and baseline_snapshot is not None:
                snapshot_requirement_ids = id_list(baseline_snapshot, "requirement_ids", location, warnings)
                classified_ids = [
                    str(value.get("requirement_id", "")).strip()
                    for value in baseline_scope
                    if isinstance(value, dict)
                ]
                if sorted(classified_ids) != sorted(snapshot_requirement_ids):
                    warnings.append(f"{location}: every BaselineSnapshot requirement must be scoped exactly once")
                applicable_ids = {
                    str(value.get("requirement_id", "")).strip()
                    for value in baseline_scope
                    if isinstance(value, dict) and value.get("status") == "applicable"
                }
                if applicable_ids != set(baseline_ids):
                    warnings.append(f"{location}: applicable baseline scope differs from baseline_requirement_ids")
            elif isinstance(baseline_scope, list) and baseline_scope:
                warnings.append(f"{location}: review without accepted baseline contains baseline scope classifications")

            comparison_alternative_ids = [
                str(value.get("alternative_id", "")).strip()
                for value in review.get("alternative_comparisons", [])
                if isinstance(value, dict)
            ]
            if set(comparison_alternative_ids) != set(alternatives) or len(comparison_alternative_ids) != len(
                set(comparison_alternative_ids)
            ):
                warnings.append(f"{location}: every Alternative must have exactly one comparison matrix")
            scenario_alternative_ids = [
                str(value.get("alternative_id", "")).strip()
                for value in review.get("management_scenarios", [])
                if isinstance(value, dict)
            ]
            if set(scenario_alternative_ids) != set(alternatives) or len(scenario_alternative_ids) != len(
                set(scenario_alternative_ids)
            ):
                warnings.append(f"{location}: every Alternative must have exactly one management scenario")

            quote_id = str(review.get("quote_id", "")).strip()
            for comparison in review.get("price_comparisons", []):
                if not isinstance(comparison, dict):
                    continue
                subject_id = str(comparison.get("subject_id", "")).strip()
                if subject_id != quote_id and subject_id not in alternatives:
                    warnings.append(f"{location}: price comparison has an unrelated subject")
                for observation_id in id_list(comparison, "price_observation_ids", location, warnings):
                    if observation_id not in price_observations:
                        warnings.append(f"{location}: price comparison links an unknown PriceObservation")

            for scenario in review.get("management_scenarios", []):
                if not isinstance(scenario, dict):
                    continue
                scenario_status = scenario.get("status")
                cost_plan_id = str(scenario.get("cost_plan_id", "")).strip()
                schedule_plan_id = str(scenario.get("schedule_plan_id", "")).strip()
                change_id = str(scenario.get("change_impact_assessment_id", "")).strip()
                cost_plan = cost_plans.get(cost_plan_id)
                schedule_plan = schedule_plans.get(schedule_plan_id)
                if cost_plan_id and cost_plan is None:
                    warnings.append(f"{location}: management scenario links an unknown CostPlan")
                if schedule_plan_id and schedule_plan is None:
                    warnings.append(f"{location}: management scenario links an unknown SchedulePlan")
                if change_id and change_id not in change_impacts:
                    warnings.append(f"{location}: management scenario links an unknown ChangeImpactAssessment")
                if scenario_status == "complete":
                    if cost_plan is None or cost_plan.get("status") != "ready_for_baseline":
                        warnings.append(f"{location}: complete management scenario requires a ready CostPlan")
                    if schedule_plan is None or schedule_plan.get("status") != "ready_for_baseline":
                        warnings.append(f"{location}: complete management scenario requires a ready SchedulePlan")
                    if baseline_mode == "accepted_baseline" and baseline_snapshot_id:
                        if cost_plan is not None and cost_plan.get("baseline_snapshot_id") != baseline_snapshot_id:
                            warnings.append(f"{location}: management scenario CostPlan uses another baseline")
                        if schedule_plan is not None and schedule_plan.get("baseline_snapshot_id") != baseline_snapshot_id:
                            warnings.append(f"{location}: management scenario SchedulePlan uses another baseline")

            linked_coordination_ids = id_list(review, "coordination_run_ids", location, warnings)
            for coordination_id in linked_coordination_ids:
                coordination = coordination_runs.get(coordination_id)
                if coordination is None or coordination.get("status") != "complete":
                    warnings.append(f"{location}: coordination_run_ids contains an incomplete run")
            package_ids = id_list(review, "project_package_ids", location, warnings)
            if len(package_ids) > 1 and not any(
                set(package_ids).issubset(set(coordination_runs[value].get("package_ids", [])))
                for value in linked_coordination_ids
                if value in coordination_runs
            ):
                warnings.append(f"{location}: linked packages have no complete joint CoordinationRun")
        for error in validate_review_contract(
            review,
            registered_ids,
            jsonl_ids["alternatives.jsonl"],
            jsonl_ids["findings.jsonl"],
            {
                fact_id: str(fact.get("verification_status", "")).strip()
                for fact_id, fact in facts.items()
            },
        ):
            warnings.append(f"{location}: {error}")
        if current_contract:
            scope_items = review.get("scope_boundary_matrix", [])
            scoped_quote_items: list[str] = []
            if isinstance(scope_items, list):
                for scope_number, scope in enumerate(scope_items, 1):
                    if not isinstance(scope, dict):
                        continue
                    linked_quote_items = id_list(
                        scope,
                        "quote_item_ids",
                        f"{location}:scope {scope_number}",
                        warnings,
                    )
                    linked_requirements = id_list(
                        scope,
                        "requirement_ids",
                        f"{location}:scope {scope_number}",
                        warnings,
                    )
                    if any(value not in current_quote_items for value in linked_quote_items):
                        warnings.append(f"{location}: scope boundary links an item from another quote")
                    if any(value not in jsonl_ids["approved_requirements.jsonl"] for value in linked_requirements):
                        warnings.append(f"{location}: scope boundary links an unknown requirement")
                    scoped_quote_items.extend(linked_quote_items)
            if set(scoped_quote_items) != current_quote_items or len(scoped_quote_items) != len(set(scoped_quote_items)):
                warnings.append(f"{location}: scope boundary must classify every quote item exactly once")
        blockers = id_list(review, "essential_blockers", location, warnings)
        id_list(review, "contractor_questions", location, warnings)
        if status == "ready_for_owner":
            if (
                inventory is None
                or inventory.get("status") != "complete"
                or not complete_run
                or (current_contract and not complete_extraction)
                or blockers
                or (current_contract and baseline_mode not in DIMENSIONS["baseline_assessment_mode"])
                or (current_contract and baseline_mode == "accepted_baseline" and not baseline_ids)
                or not current_quote_items
            ):
                warnings.append(f"{location}: ready review still has incomplete coverage or essential blockers")
            if not searches or not any(search.get("status") in {"complete", "partial"} for search in searches):
                warnings.append(f"{location}: ready review has no performed external search")
            if current_contract and quote is not None:
                contractor_id = str(quote.get("contractor_id", "")).strip()
                supplier_id = str(quote.get("supplier_id", "")).strip()
                quoted_counterparty_id = contractor_id or supplier_id
                distinct_counterparty_found = any(
                    candidate != quoted_counterparty_id for candidate in comparable_candidate_ids
                )
                if not distinct_counterparty_found:
                    warnings.append(f"{location}: ready review has no distinct comparable contractor or supplier candidate")


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
    proposal_document_ids: set[str] = set()
    document_versions: dict[str, set[tuple[object, str]]] = {}
    document_paths: dict[str, str] = {}
    current_document_versions: dict[str, tuple[object, str]] = {}
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
        relative_path = str(item.get("relative_path", "")).replace("\\", "/").strip("/")
        document_paths[document_id] = relative_path
        if relative_path.split("/", 1)[0].casefold() == "03_Коммерческие_предложения".casefold():
            proposal_document_ids.add(document_id)
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
        if versions:
            current_document_versions[document_id] = max(
                versions, key=lambda value: value[0] if isinstance(value[0], int) else -1
            )
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

    management_records = {
        key: [record for _, record in jsonl_records[filename]]
        for key, (filename, _) in MANAGEMENT_REGISTRIES.items()
    }
    registered_sources = {project_id, *active_documents}
    for identifiers in jsonl_ids.values():
        registered_sources.update(identifiers)
    for filename, id_field in CSV_IDS.items():
        registered_sources.update(
            row.get(id_field, "").strip()
            for row in tables[filename]
            if row.get(id_field, "").strip()
        )
    _, management_errors = validate_and_enrich(
        management_records,
        {
            "sources": registered_sources,
            "decisions": jsonl_ids["decisions.jsonl"],
            "baseline_snapshots": jsonl_ids["baseline_snapshots.jsonl"],
            "project_packages": jsonl_ids["project_packages.jsonl"],
            "quote_items": jsonl_ids["quote_items.jsonl"],
            "price_observations": jsonl_ids["price_observations.jsonl"],
            "work_items": {
                row.get("work_item_id", "").strip()
                for row in tables["work_items.csv"]
                if row.get("work_item_id", "").strip()
            },
            "changes": {
                row.get("change_id", "").strip()
                for row in tables["changes.csv"]
                if row.get("change_id", "").strip()
            },
            "approved_changes": {
                row.get("change_id", "").strip()
                for row in tables["changes.csv"]
                if row.get("change_id", "").strip() and row.get("status", "").strip() == "approved"
            },
            "cost_rows": {
                row.get("cost_id", "").strip(): row
                for row in tables["costs.csv"]
                if row.get("cost_id", "").strip()
            },
            "active_documents": active_documents,
        },
    )
    warnings.extend(f"management-cycle: {error}" for error in management_errors)

    validate_fact_records(jsonl_records, jsonl_ids, active_documents, document_versions, warnings)

    inventories_by_source: dict[tuple[str, object, str], dict] = {}
    for line_number, record in jsonl_records["document_inventories.jsonl"]:
        location = f"document_inventories.jsonl:{line_number}"
        source_id = str(record.get("source_document_id", "")).strip()
        version_key = (record.get("document_version"), str(record.get("sha256", "")).strip())
        if source_id not in active_documents:
            warnings.append(f"{location}: source document is not active")
        elif version_key not in document_versions.get(source_id, set()):
            warnings.append(f"{location}: inventory does not match a registered source version")
        if record.get("status") not in DIMENSIONS["document_inventory_status"]:
            warnings.append(f"{location}: unknown document inventory status")
        expected_units = normalized_unit_set(record.get("expected_units"))
        if record.get("status") == "complete" and expected_units is None:
            warnings.append(f"{location}: complete inventory has invalid expected_units")
        if not str(record.get("method", "")).strip() or not str(record.get("method_version", "")).strip():
            warnings.append(f"{location}: inventory method and version are required")
        if source_id and version_key[0] and version_key[1]:
            key = (source_id, version_key[0], version_key[1])
            prior = inventories_by_source.get(key)
            if prior is None or record.get("status") == "complete":
                inventories_by_source[key] = record

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
            inventory = inventories_by_source.get((source_id, version_key[0], version_key[1]))
            if inventory is None or inventory.get("status") != "complete":
                warnings.append(f"reading_runs.jsonl:{line_number}: complete run has no current complete document inventory")
            elif isinstance(coverage, dict) and normalized_unit_set(coverage.get("expected_units")) != normalized_unit_set(
                inventory.get("expected_units")
            ):
                warnings.append(f"reading_runs.jsonl:{line_number}: expected_units do not match the document inventory")
            if inventory is not None and isinstance(coverage, dict):
                requirements = inventory.get("reading_requirements", [])
                checked_requirements = coverage.get("checked_requirements", [])
                if (
                    not isinstance(requirements, list)
                    or not isinstance(checked_requirements, list)
                    or any(not isinstance(value, str) or not value.strip() for value in requirements)
                    or any(not isinstance(value, str) or not value.strip() for value in checked_requirements)
                    or len(checked_requirements) != len(set(checked_requirements))
                    or set(checked_requirements) != set(requirements)
                ):
                    warnings.append(f"reading_runs.jsonl:{line_number}: visual or structural reading requirements are unchecked")

    complete_read_versions: set[tuple[str, object, str]] = set()
    summaries_root = (root / ".home-control" / "summaries").resolve()
    for _, record in jsonl_records["reading_runs.jsonl"]:
        if record.get("status") != "complete":
            continue
        source_id = str(record.get("source_document_id", "")).strip()
        version = record.get("document_version")
        sha256 = str(record.get("sha256", "")).strip()
        inventory = inventories_by_source.get((source_id, version, sha256))
        coverage = record.get("coverage")
        summary_path = str(record.get("summary_path", "")).strip()
        summary = root / summary_path if summary_path else None
        try:
            summary_resolved = summary.resolve() if summary else None
            summary_valid = bool(
                summary_resolved
                and summaries_root in summary_resolved.parents
                and summary_resolved.is_file()
                and summary is not None
                and not is_linklike(summary)
            )
        except (OSError, RuntimeError):
            summary_valid = False
        inventory_requirements = inventory.get("reading_requirements", []) if inventory else None
        checked_requirements = coverage.get("checked_requirements", []) if isinstance(coverage, dict) else None
        requirements_valid = bool(
            isinstance(inventory_requirements, list)
            and isinstance(checked_requirements, list)
            and all(isinstance(value, str) and value.strip() for value in inventory_requirements)
            and all(isinstance(value, str) and value.strip() for value in checked_requirements)
            and len(checked_requirements) == len(set(checked_requirements))
            and set(checked_requirements) == set(inventory_requirements)
        )
        if (
            source_id in active_documents
            and (version, sha256) in document_versions.get(source_id, set())
            and inventory is not None
            and inventory.get("status") == "complete"
            and complete_coverage_is_valid(coverage)
            and normalized_unit_set(coverage.get("expected_units")) == normalized_unit_set(inventory.get("expected_units"))
            and requirements_valid
            and summary_valid
        ):
            complete_read_versions.add((source_id, version, sha256))

    validate_analysis_layer(
        jsonl_records,
        jsonl_ids,
        active_documents,
        document_versions,
        complete_read_versions,
        warnings,
    )
    validate_context_layer(
        root,
        jsonl_records,
        jsonl_ids,
        active_documents,
        document_versions,
        document_paths,
        complete_read_versions,
        warnings,
    )
    validate_regulatory_layer(jsonl_records, jsonl_ids, active_documents, warnings)

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
        snapshot_id = str(record.get("baseline_snapshot_id", "")).strip()
        if snapshot_id and snapshot_id not in jsonl_ids["baseline_snapshots.jsonl"]:
            warnings.append(f"approved_requirements.jsonl:{line_number}: unknown baseline_snapshot_id")
        if not str(record.get("statement", "")).strip() or not str(record.get("scope", "")).strip():
            warnings.append(f"approved_requirements.jsonl:{line_number}: atomic statement and scope are required")
        if record.get("verification_status") not in DIMENSIONS["verification_status"]:
            warnings.append(f"approved_requirements.jsonl:{line_number}: missing or unknown verification_status")
        for fact_id in source_fact_ids:
            fact = records_by_id(jsonl_records["facts.jsonl"], "fact_id").get(fact_id)
            if fact is not None and not str(fact.get("locator", "")).strip():
                warnings.append(f"approved_requirements.jsonl:{line_number}: source fact {fact_id} has no precise locator")

    validate_baseline_snapshots(
        jsonl_records,
        jsonl_ids,
        document_versions,
        complete_read_versions,
        proposal_document_ids,
        warnings,
    )

    validate_proposal_reviews(
        root,
        jsonl_records,
        jsonl_ids,
        active_documents,
        current_document_versions,
        document_versions,
        complete_read_versions,
        warnings,
    )

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
        source_id = str(record.get("source_document_id", "")).strip()
        if source_id not in active_documents:
            warnings.append(f"quotes.jsonl:{line_number}: quote has no active source document")
        version_key = (record.get("document_version"), str(record.get("sha256", "")).strip())
        if version_key not in document_versions.get(source_id, set()):
            warnings.append(f"quotes.jsonl:{line_number}: quote is not bound to an exact registered source version")
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
        for field in (
            "observed_at",
            "region",
            "currency",
            "tax_context",
            "delivery_context",
            "availability_context",
            "source_url",
        ):
            if not str(record.get(field, "")).strip():
                warnings.append(f"{location}: missing external price context {field}")

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
        markdown, pdf = write_report_pair(report, output, "Проверка данных проекта", replace=True)
        print(markdown)
        print(pdf)
    else:
        print(output, end="")
    return 1 if warnings else 0


if __name__ == "__main__":
    raise SystemExit(main())
