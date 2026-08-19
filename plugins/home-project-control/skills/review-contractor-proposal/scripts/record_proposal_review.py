#!/usr/bin/env python3
"""Preview or atomically append a validated professional proposal-review package."""

from __future__ import annotations

import argparse
import json
import shutil
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path


SCRIPT_DIR = Path(__file__).resolve().parent
MANAGE_SCRIPTS = SCRIPT_DIR.parents[1] / "manage-project-evidence" / "scripts"
sys.path.insert(0, str(MANAGE_SCRIPTS))

from audit_project import PROPOSAL_CONTRACT, audit, validate_review_contract  # noqa: E402
from inspect_project import is_linklike, require_ready_project  # noqa: E402


PLUGIN_ROOT = SCRIPT_DIR.parents[2]
ONTOLOGY = json.loads((PLUGIN_ROOT / "schemas" / "ontology.json").read_text(encoding="utf-8"))
DIMENSIONS = {name: set(values) for name, values in ONTOLOGY["dimensions"].items()}

PACKAGE_SCHEMA_VERSION = "1.0"
REGISTRY_KEYS = {
    "reading_runs": ("reading_runs.jsonl", "reading_run_id"),
    "facts": ("facts.jsonl", "fact_id"),
    "contractors": ("contractors.jsonl", "contractor_id"),
    "suppliers": ("suppliers.jsonl", "supplier_id"),
    "quotes": ("quotes.jsonl", "quote_id"),
    "quote_items": ("quote_items.jsonl", "quote_item_id"),
    "equipment_options": ("equipment_options.jsonl", "equipment_option_id"),
    "price_observations": ("price_observations.jsonl", "price_observation_id"),
    "norm_references": ("norm_references.jsonl", "norm_reference_id"),
    "findings": ("findings.jsonl", "finding_id"),
    "alternatives": ("alternatives.jsonl", "alternative_id"),
    "project_packages": ("project_packages.jsonl", "package_id"),
    "fact_extraction_runs": ("fact_extraction_runs.jsonl", "extraction_run_id"),
    "information_gaps": ("information_gaps.jsonl", "gap_id"),
    "shared_resources": ("shared_resources.jsonl", "resource_id"),
    "resource_demands": ("resource_demands.jsonl", "demand_id"),
    "package_interfaces": ("package_interfaces.jsonl", "package_interface_id"),
    "coordination_issues": ("coordination_issues.jsonl", "coordination_issue_id"),
    "coordination_runs": ("coordination_runs.jsonl", "coordination_run_id"),
    "proposal_reviews": ("proposal_reviews.jsonl", "proposal_review_id"),
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
        if identifier in by_id:
            raise ValueError(f"{path.name}:{line_number}: duplicate {id_field} {identifier}")
        records.append(value)
        by_id[identifier] = value
    return records, by_id


def string_list(value: object, location: str, allow_empty: bool = True) -> list[str]:
    if not isinstance(value, list):
        raise ValueError(f"{location} must be an array")
    result = []
    for item in value:
        if not isinstance(item, str) or not item.strip():
            raise ValueError(f"{location} contains an empty or non-string value")
        result.append(item.strip())
    if not allow_empty and not result:
        raise ValueError(f"{location} must not be empty")
    if len(result) != len(set(result)):
        raise ValueError(f"{location} contains duplicates")
    return result


def canonical_set(value: object) -> set[str] | None:
    if not isinstance(value, list):
        return None
    normalized = [json.dumps(item, ensure_ascii=False, sort_keys=True) for item in value]
    return set(normalized) if len(normalized) == len(set(normalized)) else None


def complete_run_matches(root: Path, run: dict, inventory: dict, document_id: str, version: object, sha256: str) -> bool:
    if (
        run.get("status") != "complete"
        or run.get("source_document_id") != document_id
        or run.get("document_version") != version
        or run.get("sha256") != sha256
        or inventory.get("status") != "complete"
        or inventory.get("source_document_id") != document_id
        or inventory.get("document_version") != version
        or inventory.get("sha256") != sha256
    ):
        return False
    coverage = run.get("coverage")
    if not isinstance(coverage, dict) or coverage.get("gaps") != []:
        return False
    expected = canonical_set(coverage.get("expected_units"))
    checked = canonical_set(coverage.get("checked_units"))
    inventoried = canonical_set(inventory.get("expected_units"))
    if expected is None or checked is None or not inventoried or expected != checked or expected != inventoried:
        return False
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
        return False
    summary_path = run.get("summary_path")
    if not isinstance(summary_path, str) or not summary_path.strip():
        return False
    summary = root / summary_path
    summaries_root = (root / ".home-control" / "summaries").resolve()
    try:
        resolved = summary.resolve()
    except (OSError, RuntimeError):
        return False
    return summaries_root in resolved.parents and resolved.is_file() and not is_linklike(summary)


def load_package(path: Path) -> dict:
    if is_linklike(path) or not path.is_file():
        raise ValueError("Package path must be a regular non-linked JSON file")
    package = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(package, dict):
        raise ValueError("Review package must be a JSON object")
    if package.get("schema_version") != PACKAGE_SCHEMA_VERSION:
        raise ValueError(f"Review package schema_version must be {PACKAGE_SCHEMA_VERSION}")
    unknown = set(package) - {"schema_version", *REGISTRY_KEYS}
    if unknown:
        raise ValueError("Unknown review package sections: " + ", ".join(sorted(unknown)))
    return package


def document_index(
    root: Path,
) -> tuple[dict[str, dict], dict[str, tuple[int, str]], dict[str, set[tuple[object, str]]]]:
    registry = json.loads((root / ".home-control" / "documents.json").read_text(encoding="utf-8"))
    active: dict[str, dict] = {}
    versions: dict[str, tuple[int, str]] = {}
    all_versions: dict[str, set[tuple[object, str]]] = {}
    for document in registry.get("items", []):
        if not isinstance(document, dict) or document.get("status") != "active":
            continue
        document_id = document.get("document_id")
        history = document.get("versions", [])
        if isinstance(document_id, str) and isinstance(history, list) and history:
            current = max(history, key=lambda item: item.get("version", 0))
            active[document_id] = document
            versions[document_id] = (current.get("version"), current.get("sha256"))
            all_versions[document_id] = {
                (item.get("version"), str(item.get("sha256", "")).strip())
                for item in history
                if isinstance(item, dict)
            }
    return active, versions, all_versions


def validate_package(root: Path, package: dict) -> tuple[dict[str, list[dict]], dict[str, dict[str, dict]]]:
    existing: dict[str, dict[str, dict]] = {}
    existing_lists: dict[str, list[dict]] = {}
    additions: dict[str, list[dict]] = {}

    for key, (filename, id_field) in REGISTRY_KEYS.items():
        current_list, current_by_id = read_jsonl(root / ".home-control" / filename, id_field)
        existing[key] = current_by_id
        existing_lists[key] = current_list
        values = package.get(key, [])
        if not isinstance(values, list) or any(not isinstance(value, dict) for value in values):
            raise ValueError(f"Package section {key} must be an array of objects")
        seen: set[str] = set()
        additions[key] = []
        for index, record in enumerate(values, 1):
            identifier = record.get(id_field)
            if not isinstance(identifier, str) or not identifier.strip():
                raise ValueError(f"{key}[{index}] must have a non-empty {id_field}")
            identifier = identifier.strip()
            if identifier in seen:
                raise ValueError(f"Package section {key} repeats {id_field} {identifier}")
            seen.add(identifier)
            prior = current_by_id.get(identifier)
            if prior is not None:
                if prior != record:
                    raise ValueError(f"Existing {id_field} {identifier} has different content")
                continue
            additions[key].append(record)

    known = {
        key: {**existing[key], **{record[REGISTRY_KEYS[key][1]]: record for record in additions[key]}}
        for key in REGISTRY_KEYS
    }
    active_documents, current_versions, all_document_versions = document_index(root)
    requirements = read_jsonl(root / ".home-control" / "approved_requirements.jsonl", "requirement_id")[1]
    baseline_snapshots = read_jsonl(
        root / ".home-control" / "baseline_snapshots.jsonl", "baseline_snapshot_id"
    )[1]
    inventories = read_jsonl(root / ".home-control" / "document_inventories.jsonl", "inventory_id")[1]
    compliance_assessments = read_jsonl(
        root / ".home-control" / "compliance_assessments.jsonl", "compliance_assessment_id"
    )[1]
    superseded_baseline_ids = {
        str(snapshot.get("supersedes_baseline_snapshot_id", "")).strip()
        for snapshot in baseline_snapshots.values()
        if str(snapshot.get("supersedes_baseline_snapshot_id", "")).strip()
    }
    current_baseline_ids = set(baseline_snapshots) - superseded_baseline_ids

    for record in additions["reading_runs"]:
        document_id = str(record.get("source_document_id", "")).strip()
        if document_id not in active_documents:
            raise ValueError(f"ReadingRun {record['reading_run_id']} has no active source document")
        if (record.get("document_version"), record.get("sha256")) != current_versions[document_id]:
            raise ValueError(f"ReadingRun {record['reading_run_id']} is not bound to the current document version")
        if record.get("status") not in DIMENSIONS["reading_status"]:
            raise ValueError(f"ReadingRun {record['reading_run_id']} has an unknown status")
        if record.get("status") == "complete":
            matching_inventory = next(
                (
                    inventory
                    for inventory in inventories.values()
                    if inventory.get("source_document_id") == document_id
                    and inventory.get("document_version") == record.get("document_version")
                    and inventory.get("sha256") == record.get("sha256")
                    and inventory.get("status") == "complete"
                ),
                None,
            )
            if matching_inventory is None:
                raise ValueError(f"Complete ReadingRun {record['reading_run_id']} has no current complete inventory")
            coverage = record.get("coverage", {})
            if not isinstance(coverage, dict) or coverage.get("expected_units") != matching_inventory.get("expected_units"):
                raise ValueError(f"ReadingRun {record['reading_run_id']} does not use inventory expected_units")
            expected_units = coverage.get("expected_units", []) if isinstance(coverage, dict) else None
            checked_units = coverage.get("checked_units", []) if isinstance(coverage, dict) else None
            if not isinstance(expected_units, list) or not isinstance(checked_units, list):
                raise ValueError(f"ReadingRun {record['reading_run_id']} has invalid coverage arrays")
            if {json.dumps(item, ensure_ascii=False, sort_keys=True) for item in checked_units} != {
                json.dumps(item, ensure_ascii=False, sort_keys=True) for item in expected_units
            }:
                raise ValueError(f"ReadingRun {record['reading_run_id']} has incomplete checked_units")
            if coverage.get("gaps"):
                raise ValueError(f"ReadingRun {record['reading_run_id']} has unresolved gaps")
            if not complete_run_matches(
                root, record, matching_inventory, document_id, record.get("document_version"), str(record.get("sha256", ""))
            ):
                raise ValueError(f"Complete ReadingRun {record['reading_run_id']} does not satisfy its current inventory")

    complete_read_versions: set[tuple[str, object, str]] = set()
    for run in known["reading_runs"].values():
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
            ),
            None,
        )
        if inventory is not None and complete_run_matches(root, run, inventory, document_id, version, sha256):
            complete_read_versions.add((document_id, version, sha256))

    contractors = known["contractors"]
    suppliers = known["suppliers"]
    for record in additions["quotes"]:
        document_id = str(record.get("source_document_id", "")).strip()
        if document_id not in active_documents:
            raise ValueError(f"Quote {record['quote_id']} has no active source document")
        if (record.get("document_version"), record.get("sha256")) != current_versions[document_id]:
            raise ValueError(f"Quote {record['quote_id']} is not bound to the current source version")
        contractor_id = str(record.get("contractor_id", "")).strip()
        supplier_id = str(record.get("supplier_id", "")).strip()
        if bool(contractor_id) == bool(supplier_id):
            raise ValueError(f"Quote {record['quote_id']} must identify exactly one commercial counterparty")
        if contractor_id and contractor_id not in contractors:
            raise ValueError(f"Quote {record['quote_id']} refers to an unknown contractor")
        if supplier_id and supplier_id not in suppliers:
            raise ValueError(f"Quote {record['quote_id']} refers to an unknown supplier")

    for record in additions["quote_items"]:
        if record.get("quote_id") not in known["quotes"]:
            raise ValueError(f"QuoteItem {record['quote_item_id']} refers to an unknown quote")
        if not str(record.get("raw_text", "")).strip() or not str(record.get("locator", "")).strip():
            raise ValueError(f"QuoteItem {record['quote_item_id']} requires raw_text and locator")
        if record.get("proposal_match_status") not in DIMENSIONS["proposal_match_status"]:
            raise ValueError(f"QuoteItem {record['quote_item_id']} has an unknown proposal_match_status")
        if record.get("verifiability") not in DIMENSIONS["verifiability"]:
            raise ValueError(f"QuoteItem {record['quote_item_id']} has an unknown verifiability")
        for requirement_id in string_list(record.get("approved_requirement_ids", []), f"QuoteItem {record['quote_item_id']}.approved_requirement_ids"):
            if requirement_id not in requirements:
                raise ValueError(f"QuoteItem {record['quote_item_id']} refers to an unknown requirement")
        quantity, unit_price, amount = record.get("quantity"), record.get("unit_price"), record.get("amount")
        if all(isinstance(value, (int, float)) and not isinstance(value, bool) for value in (quantity, unit_price, amount)):
            if abs(quantity * unit_price - amount) > max(0.01, abs(amount) * 0.000001):
                raise ValueError(f"QuoteItem {record['quote_item_id']} has inconsistent arithmetic")

    registered_ids = (
        set(active_documents)
        | set(requirements)
        | set(inventories)
        | set(baseline_snapshots)
        | set(compliance_assessments)
    )
    for mapping in known.values():
        registered_ids.update(mapping)

    for review in additions["proposal_reviews"]:
        review_id = review["proposal_review_id"]
        if review.get("status") not in DIMENSIONS["proposal_review_status"]:
            raise ValueError(f"ProposalReview {review_id} has an unknown status")
        document_id = str(review.get("source_document_id", "")).strip()
        if document_id not in active_documents:
            raise ValueError(f"ProposalReview {review_id} has no active source document")
        if (review.get("document_version"), review.get("sha256")) != current_versions[document_id]:
            raise ValueError(f"ProposalReview {review_id} is not bound to the current source version")
        quote_id = str(review.get("quote_id", "")).strip()
        quote = known["quotes"].get(quote_id)
        if (
            quote is None
            or quote.get("source_document_id") != document_id
            or (quote.get("document_version"), quote.get("sha256")) != current_versions[document_id]
        ):
            raise ValueError(f"ProposalReview {review_id} has no quote for its exact source version")
        disciplines = string_list(review.get("disciplines", []), f"ProposalReview {review_id}.disciplines", allow_empty=False)
        if not disciplines:
            raise ValueError(f"ProposalReview {review_id} must declare at least one discipline")
        inventory_id = str(review.get("inventory_id", "")).strip()
        inventory = inventories.get(inventory_id)
        if (
            inventory is None
            or inventory.get("source_document_id") != document_id
            or inventory.get("document_version") != review.get("document_version")
            or inventory.get("sha256") != review.get("sha256")
        ):
            raise ValueError(f"ProposalReview {review_id} has no current document inventory")
        reading_run_ids = string_list(review.get("reading_run_ids", []), f"ProposalReview {review_id}.reading_run_ids", allow_empty=False)
        matching_complete_run = any(
            run_id in known["reading_runs"]
            and complete_run_matches(
                root,
                known["reading_runs"][run_id],
                inventory,
                document_id,
                review.get("document_version"),
                str(review.get("sha256", "")),
            )
            for run_id in reading_run_ids
        )
        extraction_run_ids = string_list(
            review.get("fact_extraction_run_ids", []),
            f"ProposalReview {review_id}.fact_extraction_run_ids",
            allow_empty=False,
        )
        matching_complete_extraction = False
        for extraction_id in extraction_run_ids:
            extraction = known["fact_extraction_runs"].get(extraction_id)
            if extraction is None:
                raise ValueError(f"ProposalReview {review_id} refers to an unknown FactExtractionRun")
            expected_sections = string_list(
                extraction.get("expected_sections", []),
                f"FactExtractionRun {extraction_id}.expected_sections",
                allow_empty=False,
            )
            checked_sections = string_list(
                extraction.get("checked_sections", []),
                f"FactExtractionRun {extraction_id}.checked_sections",
                allow_empty=False,
            )
            fact_ids = string_list(
                extraction.get("fact_ids", []),
                f"FactExtractionRun {extraction_id}.fact_ids",
                allow_empty=False,
            )
            if any(identifier not in known["facts"] for identifier in fact_ids):
                raise ValueError(f"FactExtractionRun {extraction_id} refers to an unknown Fact")
            if (
                extraction.get("source_document_id") == document_id
                and extraction.get("document_version") == review.get("document_version")
                and extraction.get("sha256") == review.get("sha256")
                and extraction.get("status") == "complete"
                and set(expected_sections) == set(checked_sections)
                and extraction.get("coverage_gaps") == []
            ):
                matching_complete_extraction = True
        project_package_ids = string_list(
            review.get("project_package_ids", []),
            f"ProposalReview {review_id}.project_package_ids",
            allow_empty=False,
        )
        if any(identifier not in known["project_packages"] for identifier in project_package_ids):
            raise ValueError(f"ProposalReview {review_id} refers to an unknown ProjectPackage")
        for field, key in (
            ("information_gap_ids", "information_gaps"),
            ("coordination_issue_ids", "coordination_issues"),
        ):
            linked = string_list(review.get(field, []), f"ProposalReview {review_id}.{field}")
            if any(identifier not in known[key] for identifier in linked):
                raise ValueError(f"ProposalReview {review_id}.{field} contains an unknown link")
        compliance_ids = string_list(
            review.get("compliance_assessment_ids", []),
            f"ProposalReview {review_id}.compliance_assessment_ids",
        )
        if any(identifier not in compliance_assessments for identifier in compliance_ids):
            raise ValueError(f"ProposalReview {review_id} refers to an unknown ComplianceAssessment")
        normative_check = next(
            (
                value
                for value in review.get("mandatory_checks", [])
                if isinstance(value, dict) and value.get("check_id") == "norms_and_specialist_boundary"
            ),
            None,
        )
        if isinstance(normative_check, dict) and normative_check.get("status") == "completed":
            if not compliance_ids:
                raise ValueError(f"ProposalReview {review_id} completed normative check needs a ComplianceAssessment")
            if any(compliance_assessments[value].get("status") != "complete" for value in compliance_ids):
                raise ValueError(f"ProposalReview {review_id} links a non-complete ComplianceAssessment")
            normative_sources = string_list(
                normative_check.get("source_ids", []),
                f"ProposalReview {review_id} normative source_ids",
                allow_empty=False,
            )
            if not set(normative_sources) & set(compliance_ids):
                raise ValueError(f"ProposalReview {review_id} normative check does not cite its ComplianceAssessment")
        baseline_mode = review.get("baseline_assessment_mode")
        if baseline_mode not in DIMENSIONS["baseline_assessment_mode"]:
            raise ValueError(f"ProposalReview {review_id} has an unknown or missing baseline_assessment_mode")
        baseline_snapshot_id = str(review.get("baseline_snapshot_id", "")).strip()
        baseline_applicability_scope = str(review.get("baseline_applicability_scope", "")).strip()
        baseline_snapshot = baseline_snapshots.get(baseline_snapshot_id)
        baseline_ids = string_list(review.get("baseline_requirement_ids", []), f"ProposalReview {review_id}.baseline_requirement_ids")
        if any(identifier not in requirements for identifier in baseline_ids):
            raise ValueError(f"ProposalReview {review_id} refers to an unknown baseline requirement")
        if baseline_mode == "accepted_baseline":
            if baseline_snapshot is None or baseline_snapshot_id not in current_baseline_ids:
                raise ValueError(f"ProposalReview {review_id} must use the current BaselineSnapshot")
            snapshot_requirement_ids = string_list(
                baseline_snapshot.get("requirement_ids", []),
                f"BaselineSnapshot {baseline_snapshot_id}.requirement_ids",
                allow_empty=False,
            )
            if not baseline_applicability_scope:
                raise ValueError(f"ProposalReview {review_id} requires baseline_applicability_scope")
            if not baseline_ids or not set(baseline_ids).issubset(set(snapshot_requirement_ids)):
                raise ValueError(
                    f"ProposalReview {review_id} baseline requirements must be a non-empty subset of its snapshot"
                )
        elif baseline_snapshot_id or baseline_ids or baseline_applicability_scope:
            raise ValueError(f"ProposalReview {review_id} reference-only mode must not claim an accepted baseline")
        matches = review.get("requirement_matches")
        if not isinstance(matches, list) or any(not isinstance(value, dict) for value in matches):
            raise ValueError(f"ProposalReview {review_id}.requirement_matches must be an array of objects")
        matched_requirements: list[str] = []
        covered_quote_items: set[str] = set()
        quote_item_ids = {identifier for identifier, item in known["quote_items"].items() if item.get("quote_id") == quote_id}
        if baseline_mode != "accepted_baseline":
            for item_id in quote_item_ids:
                if string_list(
                    known["quote_items"][item_id].get("approved_requirement_ids", []),
                    f"QuoteItem {item_id}.approved_requirement_ids",
                ):
                    raise ValueError(f"ProposalReview {review_id} reference-only quote items cannot link approved requirements")
        for index, match in enumerate(matches, 1):
            requirement_id = str(match.get("requirement_id", "")).strip()
            if requirement_id not in requirements:
                raise ValueError(f"ProposalReview {review_id} match {index} has an unknown requirement")
            if match.get("status") not in DIMENSIONS["proposal_match_status"]:
                raise ValueError(f"ProposalReview {review_id} match {index} has an unknown status")
            linked_items = string_list(match.get("quote_item_ids", []), f"ProposalReview {review_id} match {index}.quote_item_ids")
            if any(identifier not in quote_item_ids for identifier in linked_items):
                raise ValueError(f"ProposalReview {review_id} match {index} links an item from another quote")
            matched_requirements.append(requirement_id)
            covered_quote_items.update(linked_items)
        if sorted(matched_requirements) != sorted(baseline_ids) or len(matched_requirements) != len(set(matched_requirements)):
            raise ValueError(f"ProposalReview {review_id} does not cover every baseline requirement exactly once")
        unmatched = set(string_list(review.get("unmatched_quote_item_ids", []), f"ProposalReview {review_id}.unmatched_quote_item_ids"))
        if unmatched & covered_quote_items or covered_quote_items | unmatched != quote_item_ids:
            raise ValueError(f"ProposalReview {review_id} does not classify every quote item exactly once")

        reference_comparisons = review.get("reference_comparisons", [])
        if not isinstance(reference_comparisons, list) or any(not isinstance(value, dict) for value in reference_comparisons):
            raise ValueError(f"ProposalReview {review_id}.reference_comparisons must be an array of objects")
        for number, comparison in enumerate(reference_comparisons, 1):
            document_id = str(comparison.get("document_id", "")).strip()
            version = comparison.get("document_version")
            sha256 = str(comparison.get("sha256", "")).strip()
            if (version, sha256) not in all_document_versions.get(document_id, set()):
                raise ValueError(f"ProposalReview {review_id} reference comparison {number} uses an unknown version")
            if (document_id, version, sha256) not in complete_read_versions:
                raise ValueError(f"ProposalReview {review_id} reference comparison {number} is not fully read")
            if document_id == review.get("source_document_id"):
                raise ValueError(f"ProposalReview {review_id} cannot use its proposal as a reference document")
            for field in ("project_role", "applicability_scope", "statement", "locator", "limitations"):
                if not str(comparison.get(field, "")).strip():
                    raise ValueError(f"ProposalReview {review_id} reference comparison {number} requires {field}")
            if comparison.get("status") not in DIMENSIONS["proposal_match_status"]:
                raise ValueError(f"ProposalReview {review_id} reference comparison {number} has an unknown status")
            linked_items = string_list(
                comparison.get("quote_item_ids", []),
                f"ProposalReview {review_id} reference comparison {number}.quote_item_ids",
            )
            if any(identifier not in quote_item_ids for identifier in linked_items):
                raise ValueError(f"ProposalReview {review_id} reference comparison {number} links another quote")
        baseline_limitations = string_list(
            review.get("baseline_limitations", []),
            f"ProposalReview {review_id}.baseline_limitations",
        )
        if baseline_mode == "reference_only" and not reference_comparisons:
            raise ValueError(f"ProposalReview {review_id} reference_only mode requires reference comparisons")
        if baseline_mode == "no_relevant_documents" and reference_comparisons:
            raise ValueError(f"ProposalReview {review_id} no_relevant_documents mode cannot have reference comparisons")
        if baseline_mode != "accepted_baseline" and not baseline_limitations:
            raise ValueError(f"ProposalReview {review_id} without an accepted baseline must state dependent limitations")

        checks = review.get("technical_checks")
        if not isinstance(checks, list) or not checks or any(not isinstance(value, dict) for value in checks):
            raise ValueError(f"ProposalReview {review_id} requires technical_checks")
        check_ids: set[str] = set()
        for check in checks:
            check_id = str(check.get("check_id", "")).strip()
            if not check_id or check_id in check_ids:
                raise ValueError(f"ProposalReview {review_id} has a missing or duplicate check_id")
            check_ids.add(check_id)
            if not str(check.get("category", "")).strip() or not str(check.get("criterion", "")).strip():
                raise ValueError(f"ProposalReview {review_id} check {check_id} requires category and criterion")
            if check.get("status") not in DIMENSIONS["technical_check_status"]:
                raise ValueError(f"ProposalReview {review_id} check {check_id} has an unknown status")
            sources = string_list(check.get("source_ids", []), f"ProposalReview {review_id} check {check_id}.source_ids")
            if any(identifier not in registered_ids for identifier in sources):
                raise ValueError(f"ProposalReview {review_id} check {check_id} refers to an unknown source")

        calculations = review.get("calculations", [])
        if not isinstance(calculations, list) or any(not isinstance(value, dict) for value in calculations):
            raise ValueError(f"ProposalReview {review_id}.calculations must be an array")
        for calculation in calculations:
            calculation_id = str(calculation.get("calculation_id", "")).strip()
            if not calculation_id or not str(calculation.get("formula", "")).strip():
                raise ValueError(f"ProposalReview {review_id} has a calculation without ID or formula")
            if calculation.get("status") not in DIMENSIONS["calculation_status"]:
                raise ValueError(f"ProposalReview {review_id} calculation {calculation_id} has an unknown status")
            inputs = calculation.get("inputs")
            if not isinstance(inputs, list) or any(not isinstance(value, dict) for value in inputs):
                raise ValueError(f"ProposalReview {review_id} calculation {calculation_id} has invalid inputs")
            for value in inputs:
                if not str(value.get("name", "")).strip() or "value" not in value or not str(value.get("unit", "")).strip():
                    raise ValueError(f"ProposalReview {review_id} calculation {calculation_id} has incomplete input")
                sources = string_list(value.get("source_ids", []), f"ProposalReview {review_id} calculation input source_ids")
                if any(identifier not in registered_ids for identifier in sources):
                    raise ValueError(f"ProposalReview {review_id} calculation input refers to an unknown source")

        searches = review.get("search_runs")
        if not isinstance(searches, list) or any(not isinstance(value, dict) for value in searches):
            raise ValueError(f"ProposalReview {review_id}.search_runs must be an array")
        comparable_candidate_ids: set[str] = set()
        for search in searches:
            search_id = str(search.get("search_run_id", "")).strip()
            if not search_id or search.get("status") not in DIMENSIONS["search_run_status"]:
                raise ValueError(f"ProposalReview {review_id} has an invalid search run")
            string_list(search.get("queries", []), f"ProposalReview {review_id} search {search_id}.queries", allow_empty=False)
            string_list(search.get("source_urls", []), f"ProposalReview {review_id} search {search_id}.source_urls")
            if not str(search.get("checked_at", "")).strip() or not str(search.get("region", "")).strip():
                raise ValueError(f"ProposalReview {review_id} search {search_id} requires checked_at and region")
            privacy = search.get("privacy_review")
            if not isinstance(privacy, dict) or privacy.get("unnecessary_private_data_removed") is not True:
                raise ValueError(f"ProposalReview {review_id} search {search_id} has no privacy confirmation")
            candidates = string_list(search.get("candidate_contractor_ids", []), f"ProposalReview {review_id} search candidates")
            if any(identifier not in contractors for identifier in candidates):
                raise ValueError(f"ProposalReview {review_id} search refers to an unknown contractor")
            supplier_candidates = string_list(
                search.get("candidate_supplier_ids", []),
                f"ProposalReview {review_id} search supplier candidates",
            )
            if any(identifier not in suppliers for identifier in supplier_candidates):
                raise ValueError(f"ProposalReview {review_id} search refers to an unknown supplier")
            candidate_assessments = search.get("candidate_assessments")
            if not isinstance(candidate_assessments, list) or any(
                not isinstance(value, dict) for value in candidate_assessments
            ):
                raise ValueError(f"ProposalReview {review_id} search candidate_assessments must be an array of objects")
            assessed_ids: list[str] = []
            for number, assessment in enumerate(candidate_assessments, 1):
                counterparty_id = str(assessment.get("counterparty_id", "")).strip()
                counterparty_kind = str(assessment.get("counterparty_kind", "")).strip()
                assessed_ids.append(counterparty_id)
                if counterparty_kind == "contractor":
                    if counterparty_id not in candidates:
                        raise ValueError(
                            f"ProposalReview {review_id} candidate assessment {number} refers to an unlisted contractor"
                        )
                elif counterparty_kind == "supplier":
                    if counterparty_id not in supplier_candidates:
                        raise ValueError(
                            f"ProposalReview {review_id} candidate assessment {number} refers to an unlisted supplier"
                        )
                else:
                    raise ValueError(f"ProposalReview {review_id} candidate assessment {number} has an unknown kind")
                comparability_status = assessment.get("comparability_status")
                if comparability_status not in PROPOSAL_CONTRACT["candidate_comparability_statuses"]:
                    raise ValueError(
                        f"ProposalReview {review_id} candidate assessment {number} has an unknown comparability status"
                    )
                if not str(assessment.get("basis", "")).strip():
                    raise ValueError(f"ProposalReview {review_id} candidate assessment {number} lacks a basis")
                string_list(
                    assessment.get("missing_information", []),
                    f"ProposalReview {review_id} candidate assessment {number}.missing_information",
                )
                string_list(
                    assessment.get("source_urls", []),
                    f"ProposalReview {review_id} candidate assessment {number}.source_urls",
                    allow_empty=False,
                )
                if (
                    search.get("status") in {"complete", "partial"}
                    and comparability_status in {"potentially_comparable", "requires_quote"}
                    and counterparty_id
                ):
                    comparable_candidate_ids.add(counterparty_id)
            listed_ids = [*candidates, *supplier_candidates]
            if sorted(assessed_ids) != sorted(listed_ids) or len(assessed_ids) != len(set(assessed_ids)):
                raise ValueError(f"ProposalReview {review_id} every candidate needs exactly one comparability assessment")

        finding_ids = string_list(review.get("finding_ids", []), f"ProposalReview {review_id}.finding_ids")
        if any(identifier not in known["findings"] for identifier in finding_ids):
            raise ValueError(f"ProposalReview {review_id} refers to an unknown finding")
        alternative_ids = string_list(review.get("alternative_ids", []), f"ProposalReview {review_id}.alternative_ids")
        if any(identifier not in known["alternatives"] for identifier in alternative_ids):
            raise ValueError(f"ProposalReview {review_id} refers to an unknown alternative")
        manifest = review.get("completion_manifest")
        if not isinstance(manifest, dict) or manifest.get("contract_version") != PROPOSAL_CONTRACT["contract_version"]:
            raise ValueError(
                f"ProposalReview {review_id} must use current contract version {PROPOSAL_CONTRACT['contract_version']}"
            )
        contract_errors = validate_review_contract(
            review,
            registered_ids,
            set(known["alternatives"]),
            set(known["findings"]),
        )
        if contract_errors:
            raise ValueError(f"ProposalReview {review_id} contract failed: " + "; ".join(contract_errors))
        scoped_quote_items: list[str] = []
        scope_items = review.get("scope_boundary_matrix", [])
        if not isinstance(scope_items, list):
            raise ValueError(f"ProposalReview {review_id}.scope_boundary_matrix must be an array")
        for scope_number, scope in enumerate(scope_items, 1):
            if not isinstance(scope, dict):
                continue
            linked_quote_items = string_list(
                scope.get("quote_item_ids", []),
                f"ProposalReview {review_id} scope {scope_number}.quote_item_ids",
            )
            linked_requirements = string_list(
                scope.get("requirement_ids", []),
                f"ProposalReview {review_id} scope {scope_number}.requirement_ids",
            )
            if any(identifier not in quote_item_ids for identifier in linked_quote_items):
                raise ValueError(f"ProposalReview {review_id} scope boundary links an item from another quote")
            if any(identifier not in requirements for identifier in linked_requirements):
                raise ValueError(f"ProposalReview {review_id} scope boundary links an unknown requirement")
            scoped_quote_items.extend(linked_quote_items)
        if set(scoped_quote_items) != quote_item_ids or len(scoped_quote_items) != len(set(scoped_quote_items)):
            raise ValueError(f"ProposalReview {review_id} scope boundary must classify every quote item exactly once")
        blockers = string_list(review.get("essential_blockers", []), f"ProposalReview {review_id}.essential_blockers")
        string_list(review.get("contractor_questions", []), f"ProposalReview {review_id}.contractor_questions")
        if review.get("status") == "ready_for_owner":
            if (
                inventory.get("status") != "complete"
                or not matching_complete_run
                or not matching_complete_extraction
                or blockers
                or (baseline_mode == "accepted_baseline" and not baseline_ids)
                or not quote_item_ids
            ):
                raise ValueError(
                    f"ProposalReview {review_id} cannot be ready while coverage or blockers are incomplete, "
                    "including semantic fact extraction"
                )
            if not searches or not any(search.get("status") in {"complete", "partial"} for search in searches):
                raise ValueError(f"ProposalReview {review_id} cannot be ready without a performed external search")
            contractor_id = str(quote.get("contractor_id", "")).strip()
            supplier_id = str(quote.get("supplier_id", "")).strip()
            quoted_counterparty_id = contractor_id or supplier_id
            distinct_counterparty_found = any(
                candidate != quoted_counterparty_id for candidate in comparable_candidate_ids
            )
            if not distinct_counterparty_found:
                raise ValueError(
                    f"ProposalReview {review_id} cannot be ready without a distinct comparable contractor or supplier candidate"
                )

    return additions, existing


def write_registry_atomic(path: Path, records: list[dict]) -> None:
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    try:
        text = "".join(json.dumps(record, ensure_ascii=False) + "\n" for record in records)
        temporary.write_text(text, encoding="utf-8")
        temporary.replace(path)
    finally:
        if temporary.exists():
            temporary.unlink()


def apply_package(root: Path, additions: dict[str, list[dict]]) -> dict[str, int]:
    changed = {key: records for key, records in additions.items() if records}
    if not changed:
        return {}
    before_warnings = set(audit(root))
    originals: dict[Path, bytes] = {}
    recovery_root = root / ".home-control" / "recovery" / utc_stamp() / "proposal-package"
    if is_linklike(recovery_root.parent.parent) or (
        recovery_root.parent.parent.exists() and not recovery_root.parent.parent.is_dir()
    ):
        raise ValueError("Unsafe .home-control/recovery path")
    try:
        for key in changed:
            filename, id_field = REGISTRY_KEYS[key]
            path = root / ".home-control" / filename
            originals[path] = path.read_bytes()
            backup = recovery_root / filename
            backup.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(path, backup)
            existing_list = read_jsonl(path, id_field)[0]
            write_registry_atomic(path, [*existing_list, *changed[key]])
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
    return {key: len(records) for key, records in changed.items()}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("project_dir", type=Path)
    parser.add_argument("package_json", type=Path)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()

    root = require_ready_project(args.project_dir)
    package = load_package(args.package_json.expanduser())
    additions, _ = validate_package(root, package)
    plan = {key: len(records) for key, records in additions.items() if records}
    result: dict[str, object] = {"mode": "preview", "project_root": str(root), "append": plan}
    if args.apply:
        result.update({"mode": "applied", "appended": apply_package(root, additions)})
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (OSError, UnicodeError, ValueError, json.JSONDecodeError) as exc:
        print(f"Proposal review package failed: {exc}", file=sys.stderr)
        raise SystemExit(2)
