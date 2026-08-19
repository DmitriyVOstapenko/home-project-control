#!/usr/bin/env python3
"""Audit local project registries without changing source documents."""

from __future__ import annotations

import argparse
import csv
import json
import uuid
from pathlib import Path

from inspect_project import is_linklike, require_ready_project


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


def validate_review_contract(
    review: dict,
    registered_ids: set[str],
    known_alternatives: set[str],
    known_findings: set[str] | None = None,
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
    site_statuses: list[str] = []
    contractor_statuses: list[str] = []
    cost_contract_blocked = False

    if current_contract:
        if not str(review.get("additional_analysis_summary", "")).strip():
            errors.append("additional_analysis_summary is required for the current contract")

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
            ])
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
            if foreman.get("verdict") != "conditionally_recommended":
                errors.append("ready_for_contract requires a conditionally_recommended foreman verdict")
            if cost_contract_blocked:
                errors.append("ready_for_contract has unresolved cost exposure")
            if any(status not in {"completed", "not_applicable"} for status in site_statuses):
                errors.append("ready_for_contract has open site verification")
            if any(status not in ready_statuses for status in contractor_statuses):
                errors.append("ready_for_contract has incomplete contractor assessment")
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


def validate_proposal_reviews(
    root: Path,
    jsonl_records: dict[str, list[tuple[int, dict]]],
    jsonl_ids: dict[str, set[str]],
    active_documents: set[str],
    current_versions: dict[str, tuple[object, str]],
    warnings: list[str],
) -> None:
    inventories = records_by_id(jsonl_records["document_inventories.jsonl"], "inventory_id")
    reading_runs = records_by_id(jsonl_records["reading_runs.jsonl"], "reading_run_id")
    quotes = records_by_id(jsonl_records["quotes.jsonl"], "quote_id")
    quote_items = records_by_id(jsonl_records["quote_items.jsonl"], "quote_item_id")
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

        baseline_ids = id_list(review, "baseline_requirement_ids", location, warnings)
        if any(value not in jsonl_ids["approved_requirements.jsonl"] for value in baseline_ids):
            warnings.append(f"{location}: unknown baseline requirement")
        matches = review.get("requirement_matches")
        matched_requirements: list[str] = []
        covered_items: set[str] = set()
        current_quote_items = {
            item_id for item_id, item in quote_items.items() if item.get("quote_id") == quote_id
        }
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
        for error in validate_review_contract(
            review,
            registered_ids,
            jsonl_ids["alternatives.jsonl"],
            jsonl_ids["findings.jsonl"],
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
                or blockers
                or not baseline_ids
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
    document_versions: dict[str, set[tuple[object, str]]] = {}
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

    validate_proposal_reviews(
        root,
        jsonl_records,
        jsonl_ids,
        active_documents,
        current_document_versions,
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

    for line_number, record in jsonl_records["norm_references.jsonl"]:
        location = f"norm_references.jsonl:{line_number}"
        for field in ("title", "version", "territory", "checked_at", "locator", "source_url", "scope"):
            if not str(record.get(field, "")).strip():
                warnings.append(f"{location}: missing normative source context {field}")

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
