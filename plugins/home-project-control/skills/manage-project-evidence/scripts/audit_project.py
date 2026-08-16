#!/usr/bin/env python3
"""Audit local project registries without changing source documents."""

from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path


CSV_IDS = {
    "costs.csv": "cost_id",
    "work_items.csv": "work_item_id",
    "issues.csv": "issue_id",
    "changes.csv": "change_id",
    "commitments.csv": "commitment_id",
    "acceptance.csv": "acceptance_id",
    "procurement.csv": "procurement_id",
}


def validate_project(path: Path) -> Path:
    root = path.expanduser().resolve()
    if root == Path(root.anchor) or root == Path.home().resolve():
        raise ValueError("Unsafe project directory")
    control = root / ".home-control"
    if not (control / "documents.json").is_file():
        raise FileNotFoundError(f"Project registry not found: {control / 'documents.json'}")
    return root


def read_csv(path: Path) -> list[dict[str, str]]:
    if not path.is_file():
        return []
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))


def read_jsonl_ids(path: Path, id_field: str, warnings: list[str]) -> set[str]:
    result: set[str] = set()
    if not path.is_file():
        return result
    for line_number, raw in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if not raw.strip():
            continue
        try:
            record = json.loads(raw)
        except json.JSONDecodeError as exc:
            warnings.append(f"{path.name}:{line_number}: invalid JSON: {exc.msg}")
            continue
        value = str(record.get(id_field, "")).strip()
        if not value:
            warnings.append(f"{path.name}:{line_number}: missing {id_field}")
        elif value in result:
            warnings.append(f"{path.name}:{line_number}: duplicate {id_field} {value}")
        result.add(value)
    return result


def document_ok(row: dict[str, str], field: str, active_documents: set[str]) -> bool:
    value = row.get(field, "").strip()
    return bool(value) and value in active_documents


def audit(root: Path) -> list[str]:
    control = root / ".home-control"
    warnings: list[str] = []
    documents = json.loads((control / "documents.json").read_text(encoding="utf-8"))
    active_documents = {
        str(item.get("document_id", "")).strip()
        for item in documents.get("items", [])
        if item.get("status") == "active" and item.get("document_id")
    }
    decisions = read_jsonl_ids(control / "decisions.jsonl", "decision_id", warnings)

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
        report.write_text(output, encoding="utf-8")
        print(report)
    else:
        print(output, end="")
    return 1 if warnings else 0


if __name__ == "__main__":
    raise SystemExit(main())
