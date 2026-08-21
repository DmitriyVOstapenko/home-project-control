#!/usr/bin/env python3
"""Build or save a compact evidence-linked project context card."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from audit_project import complete_coverage_is_valid, normalized_unit_set
from inspect_project import is_linklike, require_ready_project
from render_report_pdf import require_pdf_dependencies, write_report_pair


def read_jsonl(path: Path) -> list[dict]:
    records: list[dict] = []
    for line_number, raw in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if not raw.strip():
            continue
        value = json.loads(raw)
        if not isinstance(value, dict):
            raise ValueError(f"{path.name}:{line_number}: expected a JSON object")
        records.append(value)
    return records


def current_versioned(
    records: list[dict], id_field: str, supersedes_field: str, version_field: str
) -> dict | None:
    if not records:
        return None
    superseded = {
        str(record.get(supersedes_field, "")).strip()
        for record in records
        if str(record.get(supersedes_field, "")).strip()
    }
    current = [record for record in records if str(record.get(id_field, "")).strip() not in superseded]
    if len(current) == 1:
        return current[0]
    raise ValueError(f"Cannot determine one current {version_field} record")


def current_requests(records: list[dict]) -> list[dict]:
    grouped: dict[str, list[dict]] = {}
    for record in records:
        series = str(record.get("request_series_id", "")).strip()
        version = record.get("request_version")
        if not series or not isinstance(version, int):
            raise ValueError("Invalid analysis request series or version")
        grouped.setdefault(series, []).append(record)
    current: list[dict] = []
    for series, revisions in grouped.items():
        by_version = {record["request_version"]: record for record in revisions}
        if len(by_version) != len(revisions) or set(by_version) != set(range(1, len(revisions) + 1)):
            raise ValueError(f"Invalid revision sequence for analysis request series {series}")
        for version in range(2, len(revisions) + 1):
            prior = by_version[version - 1]
            revision = by_version[version]
            if revision.get("supersedes_analysis_request_id") != prior.get("analysis_request_id"):
                raise ValueError(f"Broken revision link for analysis request series {series}")
            if any(
                revision.get(field) != prior.get(field)
                for field in ("request_text", "request_type", "requested_at")
            ):
                raise ValueError(f"Changed original fields in analysis request series {series}")
        current.append(by_version[len(revisions)])
    return sorted(current, key=lambda value: str(value.get("requested_at", "")))


def bullet(value: object, fallback: str = "не указано") -> str:
    text = str(value).strip() if value is not None else ""
    return text or fallback


def build_card(root: Path) -> str:
    control = root / ".home-control"
    project = json.loads((control / "project.json").read_text(encoding="utf-8"))
    documents = json.loads((control / "documents.json").read_text(encoding="utf-8"))
    document_items = [item for item in documents.get("items", []) if isinstance(item, dict)]
    active_documents = [item for item in document_items if item.get("status") == "active"]
    reading_runs = read_jsonl(control / "reading_runs.jsonl")
    inventories = read_jsonl(control / "document_inventories.jsonl")
    extraction_runs = read_jsonl(control / "fact_extraction_runs.jsonl")
    facts = read_jsonl(control / "facts.jsonl")
    as_is_snapshots = read_jsonl(control / "as_is_snapshots.jsonl")
    baselines = read_jsonl(control / "baseline_snapshots.jsonl")
    gaps = read_jsonl(control / "information_gaps.jsonl")
    packages = read_jsonl(control / "project_packages.jsonl")
    requests = current_requests(read_jsonl(control / "analysis_requests.jsonl"))
    management_baselines = read_jsonl(control / "management_baselines.jsonl")
    control_snapshots = read_jsonl(control / "control_snapshots.jsonl")

    current_as_is = current_versioned(
        as_is_snapshots,
        "as_is_snapshot_id",
        "supersedes_as_is_snapshot_id",
        "snapshot_version",
    )
    current_baseline = current_versioned(
        baselines,
        "baseline_snapshot_id",
        "supersedes_baseline_snapshot_id",
        "baseline_version",
    )
    open_gaps = [gap for gap in gaps if gap.get("status") in {"open", "requested"}]
    active_requests = [
        request
        for request in requests
        if request.get("status") not in {"completed", "cancelled", "superseded"}
    ]
    inventories_by_key = {
        (
            str(value.get("source_document_id", "")).strip(),
            value.get("document_version"),
            str(value.get("sha256", "")).strip(),
        ): value
        for value in inventories
        if value.get("status") == "complete"
    }
    complete_read_keys: set[tuple[str, object, str]] = set()
    summaries_root = (control / "summaries").resolve()
    for run in reading_runs:
        if run.get("status") != "complete" or not complete_coverage_is_valid(run.get("coverage")):
            continue
        key = (
            str(run.get("source_document_id", "")).strip(),
            run.get("document_version"),
            str(run.get("sha256", "")).strip(),
        )
        inventory = inventories_by_key.get(key)
        coverage = run.get("coverage", {})
        if inventory is None or normalized_unit_set(coverage.get("expected_units")) != normalized_unit_set(
            inventory.get("expected_units")
        ):
            continue
        requirements = inventory.get("reading_requirements", [])
        checked = coverage.get("checked_requirements", [])
        if (
            not isinstance(requirements, list)
            or not isinstance(checked, list)
            or any(not isinstance(value, str) or not value.strip() for value in requirements)
            or any(not isinstance(value, str) or not value.strip() for value in checked)
            or set(requirements) != set(checked)
        ):
            continue
        summary = root / str(run.get("summary_path", ""))
        try:
            resolved = summary.resolve()
            summary_valid = summaries_root in resolved.parents and resolved.is_file() and not is_linklike(summary)
        except (OSError, RuntimeError):
            summary_valid = False
        if summary_valid:
            complete_read_keys.add(key)

    complete_extraction_keys: set[tuple[str, object, str]] = set()
    for run in extraction_runs:
        key = (
            str(run.get("source_document_id", "")).strip(),
            run.get("document_version"),
            str(run.get("sha256", "")).strip(),
        )
        expected = run.get("expected_sections")
        checked = run.get("checked_sections")
        gaps_value = run.get("coverage_gaps")
        fact_ids = run.get("fact_ids")
        if (
            run.get("status") == "complete"
            and key in complete_read_keys
            and isinstance(expected, list)
            and bool(expected)
            and isinstance(checked, list)
            and all(isinstance(value, str) and value.strip() for value in expected)
            and all(isinstance(value, str) and value.strip() for value in checked)
            and len(expected) == len(set(expected))
            and len(checked) == len(set(checked))
            and set(expected) == set(checked)
            and isinstance(gaps_value, list)
            and not gaps_value
            and isinstance(fact_ids, list)
            and bool(fact_ids)
        ):
            complete_extraction_keys.add(key)
    current_document_keys: set[tuple[str, object, str]] = set()
    for document in active_documents:
        versions = [value for value in document.get("versions", []) if isinstance(value, dict)]
        if not versions:
            continue
        current = max(versions, key=lambda value: value.get("version", 0))
        current_document_keys.add(
            (
                str(document.get("document_id", "")).strip(),
                current.get("version"),
                str(current.get("sha256", "")).strip(),
            )
        )

    lines = [
        "# Карточка объекта и продолжения работы",
        "",
        "## Объект",
        "",
        f"- Название: {bullet(project.get('name'))}",
        f"- Тип: {bullet(project.get('object_type'))}",
        f"- Этап: {bullet(project.get('project_stage'))}",
        f"- Местоположение: {bullet(project.get('location'))}",
        "",
        "## Готовность доказательной базы",
        "",
        f"- Активных документов: {len(active_documents)}",
        f"- Текущих версий с завершённым чтением: {len(current_document_keys & complete_read_keys)} из {len(current_document_keys)}",
        f"- Текущих версий с завершённым извлечением фактов: {len(current_document_keys & complete_extraction_keys)} из {len(current_document_keys)}",
        f"- Зарегистрированных фактов: {len(facts)}",
        f"- Инвентаризаций документов: {len(inventories)}",
        "",
        "## Контекст решений",
        "",
    ]
    if current_as_is is None:
        lines.append("- Состояние «как есть»: снимок ещё не принят владельцем")
    else:
        lines.append(
            "- Состояние «как есть»: "
            f"{current_as_is.get('as_is_snapshot_id')} · версия {current_as_is.get('snapshot_version')} · "
            f"{bullet(current_as_is.get('scope'))}"
        )
    if current_baseline is None:
        lines.append("- Базовая линия «что принято сделать»: ещё не принята владельцем")
    else:
        lines.append(
            "- Базовая линия «что принято сделать»: "
            f"{current_baseline.get('baseline_snapshot_id')} · версия {current_baseline.get('baseline_version')} · "
            f"{bullet(current_baseline.get('scope'))}"
        )
    accepted_management = [
        value for value in management_baselines
        if value.get("status") == "accepted" and isinstance(value.get("baseline_version"), int)
    ]
    current_management = max(
        accepted_management,
        key=lambda value: value["baseline_version"],
        default=None,
    )
    if current_management is None:
        lines.append("- Управленческая база «стоимость + срок»: ещё не принята владельцем")
    else:
        lines.append(
            "- Управленческая база «стоимость + срок»: "
            f"{current_management.get('management_baseline_id')} · версия "
            f"{current_management.get('baseline_version')}"
        )
        current_control = max(
            (
                value for value in control_snapshots
                if value.get("management_baseline_id") == current_management.get("management_baseline_id")
            ),
            key=lambda value: (str(value.get("data_date", "")), str(value.get("control_snapshot_id", ""))),
            default=None,
        )
        lines.append(
            "- Последний план-факт: "
            + (
                f"{current_control.get('control_snapshot_id')} · {current_control.get('data_date')} "
                f"[{current_control.get('status')}]"
                if current_control
                else "ещё не сформирован"
            )
        )
    lines.extend(["", "## Открытые вопросы", ""])
    if open_gaps:
        for gap in open_gaps[:10]:
            lines.append(
                f"- {bullet(gap.get('gap_id'))}: {bullet(gap.get('description'))} "
                f"(блокирует: {bullet(gap.get('blocked_conclusion'))})"
            )
        if len(open_gaps) > 10:
            lines.append(f"- Ещё открытых пробелов: {len(open_gaps) - 10}")
    else:
        lines.append("- Зарегистрированных открытых пробелов нет")

    lines.extend(["", "## Незавершённые запросы владельца", ""])
    if active_requests:
        for request in active_requests:
            lines.append(
                f"- {bullet(request.get('analysis_request_id'))}: {bullet(request.get('request_text'))} "
                f"[{bullet(request.get('status'))}]"
            )
    else:
        lines.append("- Незавершённых запросов нет")

    lines.extend(["", "## Пакеты проекта", ""])
    if packages:
        for package in packages[:10]:
            lines.append(
                f"- {bullet(package.get('package_id'))}: {bullet(package.get('name'))} "
                f"[{bullet(package.get('status'))}]"
            )
    else:
        lines.append("- Аналитические пакеты ещё не сформированы")

    if not active_documents:
        next_action = "Добавить первый пакет документов и показать единый план их раскладки."
    elif current_document_keys - complete_read_keys:
        next_action = "Завершить инвентаризацию, полное чтение и конспект непрочитанных текущих версий."
    elif current_document_keys - complete_extraction_keys:
        next_action = "Завершить извлечение атомарных фактов, требований, конфликтов и пробелов."
    elif current_as_is is None:
        next_action = "Показать владельцу проект снимка состояния «как есть» и запросить его принятие."
    elif current_baseline is None and any(
        request.get("request_type") == "proposal_review" for request in active_requests
    ):
        next_action = (
            "Предложить владельцу базовую линию «что принято сделать» и параллельно продолжить "
            "независимые проверки незавершённого КП."
        )
    elif active_requests:
        next_action = "Возобновить первый незавершённый запрос с учётом новых подтверждённых данных."
    elif current_baseline is None:
        next_action = "Предложить владельцу состав базовой линии «что принято сделать», не включая КП."
    else:
        next_action = "Запросить ближайший вопрос, решение или коммерческое предложение для анализа."
    lines.extend(["", "## Следующее действие", "", next_action, ""])
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("project_dir", type=Path)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    root = require_ready_project(args.project_dir)
    card = build_card(root)
    require_pdf_dependencies()
    if args.apply:
        target = root / ".home-control" / "reports" / "project-context.md"
        markdown, pdf = write_report_pair(target, card, "Карточка проекта", replace=True)
        print(json.dumps({"mode": "applied", "reports": [str(markdown), str(pdf)]}, ensure_ascii=False, indent=2))
    else:
        sys.stdout.write(card)
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (OSError, UnicodeError, ValueError, RuntimeError, json.JSONDecodeError) as exc:
        print(f"Project-context build failed: {exc}", file=sys.stderr)
        raise SystemExit(2)
