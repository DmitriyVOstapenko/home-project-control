#!/usr/bin/env python3
"""Build a Markdown project dashboard from local registries."""

from __future__ import annotations

import argparse
import csv
import json
import sys
import uuid
from collections import defaultdict
from datetime import date, datetime, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path


PLUGIN_ROOT = Path(__file__).resolve().parents[3]
MANAGE_SCRIPTS = PLUGIN_ROOT / "skills" / "manage-project-evidence" / "scripts"
sys.path.insert(0, str(MANAGE_SCRIPTS))
from inspect_project import require_ready_project  # noqa: E402


def read_csv(path: Path) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))


def read_optional_csv(path: Path) -> list[dict[str, str]]:
    return read_csv(path) if path.is_file() else []


def read_optional_jsonl(path: Path) -> list[dict]:
    if not path.is_file():
        return []
    result = []
    for raw in path.read_text(encoding="utf-8").splitlines():
        if raw.strip():
            value = json.loads(raw)
            if not isinstance(value, dict):
                raise ValueError(f"{path.name} contains a non-object record")
            result.append(value)
    return result


def iso_date(value: str) -> date | None:
    try:
        return date.fromisoformat(value.strip()) if value.strip() else None
    except ValueError:
        return None


def money(value: str) -> Decimal | None:
    try:
        return Decimal(value.replace(" ", "").replace(",", ".")) if value.strip() else None
    except InvalidOperation:
        return None


def fmt(value: Decimal) -> str:
    return f"{value:,.2f}".replace(",", " ")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("project_dir", type=Path)
    args = parser.parse_args()
    root = require_ready_project(args.project_dir)
    control = root / ".home-control"
    required = [control / "documents.json", control / "costs.csv", control / "work_items.csv", control / "issues.csv"]
    missing = [str(path) for path in required if not path.is_file()]
    if missing:
        raise FileNotFoundError("Missing project registries: " + ", ".join(missing))

    documents = json.loads((control / "documents.json").read_text(encoding="utf-8"))
    active_ids = {item["document_id"] for item in documents.get("items", []) if item.get("status") == "active"}
    costs = read_csv(control / "costs.csv")
    work = read_csv(control / "work_items.csv")
    issues = read_csv(control / "issues.csv")
    changes = read_optional_csv(control / "changes.csv")
    commitments = read_optional_csv(control / "commitments.csv")
    acceptance = read_optional_csv(control / "acceptance.csv")
    procurement = read_optional_csv(control / "procurement.csv")
    management_baselines = read_optional_jsonl(control / "management_baselines.jsonl")
    control_snapshots = read_optional_jsonl(control / "control_snapshots.jsonl")
    confirmed: dict[str, Decimal] = defaultdict(Decimal)
    other: dict[tuple[str, str], Decimal] = defaultdict(Decimal)
    evidence_warnings = []
    invalid_amounts = []

    for row in costs:
        amount = money(row.get("amount", ""))
        currency = row.get("currency", "").strip() or "Валюта не указана"
        status = row.get("status", "").strip() or "unknown"
        if amount is None:
            if row.get("amount", "").strip():
                invalid_amounts.append(row.get("cost_id", "без ID"))
            continue
        if status == "confirmed_paid":
            document_id = row.get("evidence_document_id", "").strip()
            locator = row.get("evidence_locator", "").strip()
            if document_id in active_ids and locator:
                confirmed[currency] += amount
            else:
                evidence_warnings.append(row.get("cost_id", "без ID"))
        else:
            other[(status, currency)] += amount

    status_counts: dict[str, int] = defaultdict(int)
    for row in work:
        status_counts[row.get("status", "").strip() or "unknown"] += 1
    open_issues = [row for row in issues if (row.get("status", "").strip() or "unknown") not in {"resolved", "closed"}]
    open_changes = [
        row for row in changes
        if (row.get("status", "").strip() or "unknown") in {"proposed", "under_review", "implemented_unapproved"}
    ]
    pending_acceptance = [
        row for row in acceptance
        if (row.get("status", "").strip() or "unknown") in {"presented", "conditionally_accepted", "specialist_check_required"}
    ]
    today = datetime.now(timezone.utc).date()
    overdue_commitments = []
    for row in commitments:
        status = row.get("status", "").strip() or "unknown"
        due = iso_date(row.get("due_date_or_event", ""))
        if status in {"open", "due"} and due is not None and due < today:
            overdue_commitments.append(row)
    active_procurement = [
        row for row in procurement
        if (row.get("status", "").strip() or "unknown") in {"approved_to_order", "ordered", "delivered"}
    ]
    now = datetime.now(timezone.utc).replace(microsecond=0).isoformat()
    lines = ["# Состояние проекта", "", f"Сформировано: {now}", "", "## Документы", "", f"- Действующих файлов в индексе: {len(active_ids)}", f"- Файлов со статусом missing: {sum(1 for item in documents.get('items', []) if item.get('status') == 'missing')}", "", "## Ход работ", ""]
    lines.extend((f"- `{status}`: {count}" for status, count in sorted(status_counts.items())) if status_counts else ["- Этапы ещё не зарегистрированы."])
    lines.extend(["", "## Подтверждённые платежи", ""])
    lines.extend((f"- {currency}: {fmt(value)}" for currency, value in sorted(confirmed.items())) if confirmed else ["- Подтверждённых платежей нет."])
    lines.extend(["", "## Прочие суммы по статусам", ""])
    lines.extend((f"- `{status}`, {currency}: {fmt(value)}" for (status, currency), value in sorted(other.items())) if other else ["- Прочих зарегистрированных сумм нет."])
    lines.extend(["", "## Открытые замечания", "", f"Всего: {len(open_issues)}", ""])
    for row in open_issues[:20]:
        lines.append(f"- {row.get('issue_id', 'без ID')}: {row.get('description', '')} [{row.get('status', 'unknown')}]")
    if len(open_issues) > 20:
        lines.append(f"- Ещё {len(open_issues) - 20} замечаний не показано.")
    lines.extend(["", "## Изменения и обязательства", "", f"- Открытых или неутверждённо выполненных изменений: {len(open_changes)}", f"- Просроченных обязательств с однозначной датой: {len(overdue_commitments)}"])
    for row in open_changes[:10]:
        lines.append(f"- Изменение {row.get('change_id', 'без ID')}: {row.get('description', '')} [{row.get('status', 'unknown')}]")
    for row in overdue_commitments[:10]:
        lines.append(f"- Обязательство {row.get('commitment_id', 'без ID')}: {row.get('description', '')} [срок {row.get('due_date_or_event', '')}]")
    lines.extend(["", "## Приёмка и закупки", "", f"- Этапов, требующих решения по приёмке: {len(pending_acceptance)}", f"- Активных закупок после допуска к заказу: {len(active_procurement)}"])
    for row in pending_acceptance[:10]:
        lines.append(f"- Приёмка {row.get('acceptance_id', 'без ID')}: этап {row.get('work_item_id', 'не указан')} [{row.get('status', 'unknown')}]")
    for row in active_procurement[:10]:
        lines.append(f"- Закупка {row.get('procurement_id', 'без ID')}: {row.get('item', '')} [{row.get('status', 'unknown')}]")
    lines.extend(["", "## Управленческая база и прогноз", ""])
    accepted_baselines = [
        value for value in management_baselines
        if value.get("status") == "accepted" and isinstance(value.get("baseline_version"), int)
    ]
    current_baseline = max(accepted_baselines, key=lambda value: value["baseline_version"], default=None)
    if current_baseline is None:
        lines.append("- Принятой управленческой базы нет; отклонение стоимости и срока не рассчитывается.")
    else:
        lines.append(
            f"- Текущая база: {current_baseline.get('management_baseline_id')} "
            f"(версия {current_baseline.get('baseline_version')})."
        )
        matching_snapshots = [
            value for value in control_snapshots
            if value.get("management_baseline_id") == current_baseline.get("management_baseline_id")
        ]
        latest_snapshot = max(
            matching_snapshots,
            key=lambda value: (str(value.get("data_date", "")), str(value.get("control_snapshot_id", ""))),
            default=None,
        )
        if latest_snapshot is None:
            lines.append("- Контрольного среза по текущей базе ещё нет.")
        else:
            metrics = latest_snapshot.get("metrics", {}) if isinstance(latest_snapshot.get("metrics"), dict) else {}
            lines.append(
                f"- Последний срез: {latest_snapshot.get('data_date', 'дата не указана')} "
                f"[{latest_snapshot.get('status', 'unknown')}]."
            )
            money_fields = (
                ("Текущий бюджет", "current_budget"),
                ("Подтверждённый факт", "confirmed_actual_cost"),
                ("Прогноз итоговой стоимости", "forecast_at_completion"),
                ("Прогнозное отклонение стоимости", "cost_variance_at_completion"),
            )
            currency = latest_snapshot.get("currency", "валюта не указана")
            for label, field in money_fields:
                value = metrics.get(field)
                lines.append(f"- {label}: {fmt(Decimal(str(value)))} {currency}." if isinstance(value, (int, float)) else f"- {label}: не рассчитано.")
            planned = metrics.get("planned_progress_percent")
            actual_progress = metrics.get("actual_progress_percent")
            lines.append(
                "- Прогресс: план "
                + (f"{planned:.2f}%" if isinstance(planned, (int, float)) else "не рассчитан")
                + ", факт "
                + (f"{actual_progress:.2f}%." if isinstance(actual_progress, (int, float)) else "не подтверждён.")
            )
            lines.append(
                f"- Завершение: база {metrics.get('baseline_finish') or 'не рассчитана'}, "
                f"прогноз {metrics.get('forecast_finish') or 'не рассчитан'}, "
                f"отклонение {metrics.get('schedule_variance_calendar_days') if metrics.get('schedule_variance_calendar_days') is not None else 'не рассчитано'} календарных дней."
            )
    lines.extend(["", "## Контроль качества данных", ""])
    if evidence_warnings:
        lines.append("- `confirmed_paid` без действующего документа или локатора: " + ", ".join(evidence_warnings))
    if invalid_amounts:
        lines.append("- Сумма не распознана: " + ", ".join(invalid_amounts))
    approved_change_warnings = [
        row.get("change_id", "без ID")
        for row in changes
        if row.get("status", "").strip() == "approved" and not row.get("decision_id", "").strip()
    ]
    if approved_change_warnings:
        lines.append("- Утверждённое изменение без ID решения владельца: " + ", ".join(approved_change_warnings))
    if not evidence_warnings and not invalid_amounts and not approved_change_warnings:
        lines.append("- Формальных ошибок в зарегистрированных суммах не найдено.")
    lines.append("")
    reports = control / "reports"
    reports.mkdir(parents=True, exist_ok=True)
    output = reports / "project-status.md"
    temporary = output.with_name(f".{output.name}.{uuid.uuid4().hex}.tmp")
    try:
        temporary.write_text("\n".join(lines), encoding="utf-8")
        temporary.replace(output)
    finally:
        if temporary.exists():
            temporary.unlink()
    print(output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
