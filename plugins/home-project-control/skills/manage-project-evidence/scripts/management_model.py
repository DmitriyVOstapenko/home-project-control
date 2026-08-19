#!/usr/bin/env python3
"""Validation and deterministic calculations for the linked management cycle."""

from __future__ import annotations

from copy import deepcopy
from datetime import date, datetime, timedelta
from decimal import Decimal, InvalidOperation
from typing import Any


REGISTRIES = {
    "cost_plans": ("cost_plans.jsonl", "cost_plan_id"),
    "schedule_plans": ("schedule_plans.jsonl", "schedule_plan_id"),
    "management_baselines": ("management_baselines.jsonl", "management_baseline_id"),
    "change_impact_assessments": (
        "change_impact_assessments.jsonl",
        "change_impact_assessment_id",
    ),
    "control_snapshots": ("control_snapshots.jsonl", "control_snapshot_id"),
}

COST_PLAN_STATUSES = {"draft", "ready_for_baseline", "superseded"}
COST_BASES = {
    "quote", "contract", "market_observation", "estimate_norm",
    "calculation", "allowance", "actual", "unknown",
}
SCHEDULE_PLAN_STATUSES = {"draft", "ready_for_baseline", "superseded"}
DATE_BASES = {"contractual", "calculated", "scenario", "actual", "unknown"}
RELATIONSHIPS = {"FS", "SS", "FF", "SF"}
BASELINE_STATUSES = {"accepted", "withdrawn"}
CHANGE_STATUSES = {"draft", "under_review", "approved", "rejected", "superseded"}
CONTROL_STATUSES = {"complete", "partial", "blocked"}
TOLERANCE = Decimal("0.01")


def decimal_value(value: Any, field: str, errors: list[str], allow_none: bool = False) -> Decimal | None:
    if value is None and allow_none:
        return None
    if isinstance(value, bool):
        errors.append(f"{field} must be a number")
        return None
    try:
        result = Decimal(str(value))
    except (InvalidOperation, ValueError, TypeError):
        errors.append(f"{field} must be a number")
        return None
    if not result.is_finite():
        errors.append(f"{field} must be finite")
        return None
    return result


def iso_date(value: Any, field: str, errors: list[str], allow_empty: bool = False) -> date | None:
    if allow_empty and (value is None or value == ""):
        return None
    try:
        return date.fromisoformat(str(value))
    except (TypeError, ValueError):
        errors.append(f"{field} must be an ISO date")
        return None


def iso_datetime(value: Any, field: str, errors: list[str]) -> datetime | None:
    try:
        return datetime.fromisoformat(str(value))
    except (TypeError, ValueError):
        errors.append(f"{field} must be an ISO date-time")
        return None


def text(value: Any) -> str:
    return str(value).strip() if value is not None else ""


def string_list(value: Any, field: str, errors: list[str]) -> list[str]:
    if not isinstance(value, list):
        errors.append(f"{field} must be an array")
        return []
    result = [text(item) for item in value]
    if any(not item for item in result) or len(result) != len(set(result)):
        errors.append(f"{field} must contain unique non-empty strings")
    return result


def require_sources(
    values: Any,
    field: str,
    known_sources: set[str],
    errors: list[str],
    required: bool,
) -> list[str]:
    result = string_list(values, field, errors)
    if required and not result:
        errors.append(f"{field} requires evidence")
    unknown = [value for value in result if value not in known_sources]
    if unknown:
        errors.append(f"{field} contains unknown source IDs: {', '.join(unknown)}")
    return result


def ensure_unique(records: list[dict], field: str, label: str, errors: list[str]) -> dict[str, dict]:
    result: dict[str, dict] = {}
    for number, record in enumerate(records, 1):
        identifier = text(record.get(field)) if isinstance(record, dict) else ""
        if not isinstance(record, dict):
            errors.append(f"{label}[{number}] must be an object")
        elif not identifier:
            errors.append(f"{label}[{number}] lacks {field}")
        elif identifier in result:
            errors.append(f"{label} has duplicate {field} {identifier}")
        else:
            result[identifier] = record
    return result


def working_calendar(plan: dict, location: str, errors: list[str]) -> tuple[list[int], set[date]]:
    calendar = plan.get("calendar", {})
    if not isinstance(calendar, dict):
        errors.append(f"{location}.calendar must be an object")
        calendar = {}
    weekdays = calendar.get("working_weekdays", [0, 1, 2, 3, 4])
    if (
        not isinstance(weekdays, list)
        or not weekdays
        or any(not isinstance(value, int) or isinstance(value, bool) or value < 0 or value > 6 for value in weekdays)
        or len(weekdays) != len(set(weekdays))
    ):
        errors.append(f"{location}.calendar.working_weekdays must contain unique integers 0..6")
        weekdays = [0, 1, 2, 3, 4]
    holidays: set[date] = set()
    raw_holidays = calendar.get("holidays", [])
    if not isinstance(raw_holidays, list):
        errors.append(f"{location}.calendar.holidays must be an array")
    else:
        for number, value in enumerate(raw_holidays, 1):
            parsed = iso_date(value, f"{location}.calendar.holidays[{number}]", errors)
            if parsed:
                holidays.add(parsed)
    return sorted(weekdays), holidays


def workday_at(start: date, offset: int, weekdays: list[int], holidays: set[date]) -> date:
    current = start
    seen = -1
    while True:
        if current.weekday() in weekdays and current not in holidays:
            seen += 1
            if seen >= offset:
                return current
        current += timedelta(days=1)


def workday_index(start: date, target: date, weekdays: list[int], holidays: set[date]) -> int:
    if target < start:
        return -1
    current = start
    index = -1
    while current <= target:
        if current.weekday() in weekdays and current not in holidays:
            index += 1
        current += timedelta(days=1)
    return index


def enrich_cost_plan(plan: dict, context: dict[str, set[str]], errors: list[str]) -> None:
    location = f"CostPlan {text(plan.get('cost_plan_id')) or '<missing>'}"
    ready = plan.get("status") == "ready_for_baseline"
    if plan.get("status") not in COST_PLAN_STATUSES:
        errors.append(f"{location} has unknown status")
    if not isinstance(plan.get("revision"), int) or isinstance(plan.get("revision"), bool) or plan["revision"] < 1:
        errors.append(f"{location}.revision must be a positive integer")
    if not text(plan.get("plan_series_id")):
        errors.append(f"{location}.plan_series_id is required")
    if not text(plan.get("currency")):
        errors.append(f"{location}.currency is required")
    iso_date(plan.get("valuation_date"), f"{location}.valuation_date", errors)
    baseline_id = text(plan.get("baseline_snapshot_id"))
    if ready and baseline_id not in context["baseline_snapshots"]:
        errors.append(f"{location} ready plan requires a known BaselineSnapshot")
    raw_items = plan.get("items")
    if not isinstance(raw_items, list) or not raw_items:
        errors.append(f"{location}.items must be a non-empty array")
        return
    items = ensure_unique(raw_items, "item_id", f"{location}.items", errors)
    total = Decimal("0")
    unknown: list[str] = []
    for item_id, item in items.items():
        item_location = f"{location}.items[{item_id}]"
        if not text(item.get("description")):
            errors.append(f"{item_location}.description is required")
        work_item_id = text(item.get("work_item_id"))
        if ready and work_item_id not in context["work_items"]:
            errors.append(f"{item_location} requires a known work_item_id")
        package_id = text(item.get("package_id"))
        if package_id and package_id not in context["project_packages"]:
            errors.append(f"{item_location} has unknown package_id")
        if item.get("cost_basis") not in COST_BASES:
            errors.append(f"{item_location} has unknown cost_basis")
        amount = decimal_value(item.get("amount"), f"{item_location}.amount", errors, allow_none=True)
        quantity = decimal_value(item.get("quantity"), f"{item_location}.quantity", errors, allow_none=True)
        unit_rate = decimal_value(item.get("unit_rate"), f"{item_location}.unit_rate", errors, allow_none=True)
        if amount is not None and amount < 0:
            errors.append(f"{item_location}.amount must be non-negative")
        if quantity is not None and unit_rate is not None and amount is not None:
            if abs(quantity * unit_rate - amount) > TOLERANCE:
                errors.append(f"{item_location}: quantity × unit_rate differs from amount")
        if amount is None:
            unknown.append(item_id)
        else:
            total += amount
        require_sources(item.get("source_ids", []), f"{item_location}.source_ids", context["sources"], errors, ready)
        for field, target in (
            ("quote_item_ids", "quote_items"),
            ("price_observation_ids", "price_observations"),
        ):
            linked = string_list(item.get(field, []), f"{item_location}.{field}", errors)
            if any(value not in context[target] for value in linked):
                errors.append(f"{item_location}.{field} contains unknown links")
    if ready and unknown:
        errors.append(f"{location} ready plan has unknown item amounts: {', '.join(unknown)}")
    declared = decimal_value(plan.get("total_amount"), f"{location}.total_amount", errors, allow_none=True)
    if declared is not None and abs(declared - total) > TOLERANCE:
        errors.append(f"{location}.total_amount differs from the sum of items")
    plan["total_amount"] = float(total)
    plan["unknown_amount_item_ids"] = unknown
    plan["calculation_formula"] = "total_amount = sum(items.amount); unknown amounts are excluded and listed"


def relationship_weight(kind: str, predecessor_duration: int, successor_duration: int, lag: int) -> int:
    if kind == "FS":
        return predecessor_duration + lag
    if kind == "SS":
        return lag
    if kind == "FF":
        return predecessor_duration - successor_duration + lag
    return -successor_duration + lag


def enrich_schedule_plan(plan: dict, context: dict[str, set[str]], errors: list[str]) -> None:
    location = f"SchedulePlan {text(plan.get('schedule_plan_id')) or '<missing>'}"
    ready = plan.get("status") == "ready_for_baseline"
    if plan.get("status") not in SCHEDULE_PLAN_STATUSES:
        errors.append(f"{location} has unknown status")
    if not isinstance(plan.get("revision"), int) or isinstance(plan.get("revision"), bool) or plan["revision"] < 1:
        errors.append(f"{location}.revision must be a positive integer")
    if not text(plan.get("plan_series_id")):
        errors.append(f"{location}.plan_series_id is required")
    baseline_id = text(plan.get("baseline_snapshot_id"))
    if ready and baseline_id not in context["baseline_snapshots"]:
        errors.append(f"{location} ready plan requires a known BaselineSnapshot")
    start = iso_date(plan.get("project_start"), f"{location}.project_start", errors)
    weekdays, holidays = working_calendar(plan, location, errors)
    raw_activities = plan.get("activities")
    if not isinstance(raw_activities, list) or not raw_activities:
        errors.append(f"{location}.activities must be a non-empty array")
        return
    activities = ensure_unique(raw_activities, "activity_id", f"{location}.activities", errors)
    durations: dict[str, int | None] = {}
    not_before_offsets: dict[str, int] = {}
    deadline_offsets: dict[str, int] = {}
    edges: dict[str, list[tuple[str, str, int]]] = {identifier: [] for identifier in activities}
    successors: dict[str, list[str]] = {identifier: [] for identifier in activities}
    indegree = {identifier: 0 for identifier in activities}
    for activity_id, activity in activities.items():
        activity_location = f"{location}.activities[{activity_id}]"
        if not text(activity.get("title")):
            errors.append(f"{activity_location}.title is required")
        work_item_id = text(activity.get("work_item_id"))
        if ready and work_item_id not in context["work_items"]:
            errors.append(f"{activity_location} requires a known work_item_id")
        package_id = text(activity.get("package_id"))
        if package_id and package_id not in context["project_packages"]:
            errors.append(f"{activity_location} has unknown package_id")
        duration = activity.get("duration_workdays")
        if duration is None:
            durations[activity_id] = None
        elif not isinstance(duration, int) or isinstance(duration, bool) or duration < 0:
            errors.append(f"{activity_location}.duration_workdays must be a non-negative integer or null")
            durations[activity_id] = None
        else:
            durations[activity_id] = duration
        if ready and durations[activity_id] is None:
            errors.append(f"{activity_location} lacks duration_workdays")
        if activity.get("date_basis") not in DATE_BASES:
            errors.append(f"{activity_location} has unknown date_basis")
        not_before = iso_date(activity.get("not_before"), f"{activity_location}.not_before", errors, allow_empty=True)
        not_after = iso_date(activity.get("not_after"), f"{activity_location}.not_after", errors, allow_empty=True)
        if start and not_before:
            not_before_offsets[activity_id] = max(0, workday_index(start, not_before, weekdays, holidays))
        if start and not_after:
            deadline_offsets[activity_id] = workday_index(start, not_after, weekdays, holidays)
        require_sources(activity.get("source_ids", []), f"{activity_location}.source_ids", context["sources"], errors, ready)
        predecessors = activity.get("predecessors", [])
        if not isinstance(predecessors, list):
            errors.append(f"{activity_location}.predecessors must be an array")
            continue
        seen_predecessors: set[tuple[str, str]] = set()
        for number, predecessor in enumerate(predecessors, 1):
            pred_location = f"{activity_location}.predecessors[{number}]"
            if not isinstance(predecessor, dict):
                errors.append(f"{pred_location} must be an object")
                continue
            pred_id = text(predecessor.get("activity_id"))
            kind = text(predecessor.get("relationship"))
            lag = predecessor.get("lag_workdays", 0)
            if pred_id not in activities or pred_id == activity_id:
                errors.append(f"{pred_location} has unknown or self predecessor")
                continue
            if kind not in RELATIONSHIPS:
                errors.append(f"{pred_location} has unknown relationship")
                continue
            if not isinstance(lag, int) or isinstance(lag, bool):
                errors.append(f"{pred_location}.lag_workdays must be an integer")
                continue
            key = (pred_id, kind)
            if key in seen_predecessors:
                errors.append(f"{pred_location} duplicates predecessor relationship")
                continue
            seen_predecessors.add(key)
            edges[activity_id].append((pred_id, kind, lag))
            successors[pred_id].append(activity_id)
            indegree[activity_id] += 1
    queue = sorted(identifier for identifier, degree in indegree.items() if degree == 0)
    order: list[str] = []
    while queue:
        current = queue.pop(0)
        order.append(current)
        for successor in successors[current]:
            indegree[successor] -= 1
            if indegree[successor] == 0:
                queue.append(successor)
                queue.sort()
    if len(order) != len(activities):
        errors.append(f"{location} contains a dependency cycle")
        return
    if start is None or any(value is None for value in durations.values()):
        plan["calculation_status"] = "blocked"
        return
    actual_durations = {key: int(value) for key, value in durations.items() if value is not None}
    earliest: dict[str, int] = {}
    weights: dict[tuple[str, str], int] = {}
    for activity_id in order:
        candidate = not_before_offsets.get(activity_id, 0)
        for pred_id, kind, lag in edges[activity_id]:
            weight = relationship_weight(kind, actual_durations[pred_id], actual_durations[activity_id], lag)
            weights[(pred_id, activity_id)] = max(weights.get((pred_id, activity_id), weight), weight)
            candidate = max(candidate, earliest[pred_id] + weight)
        earliest[activity_id] = candidate
    finish_offset = max(earliest[key] + actual_durations[key] for key in activities)
    latest = {key: finish_offset - actual_durations[key] for key in activities}
    for key, deadline in deadline_offsets.items():
        duration = actual_durations[key]
        latest[key] = min(latest[key], deadline if duration == 0 else deadline - duration + 1)
    for pred_id in reversed(order):
        for successor in successors[pred_id]:
            latest[pred_id] = min(latest[pred_id], latest[successor] - weights[(pred_id, successor)])
    infeasible = [key for key in activities if latest[key] < earliest[key]]
    if infeasible:
        errors.append(f"{location} violates declared date constraints: {', '.join(infeasible)}")
        plan["calculation_status"] = "blocked"
        return
    for activity_id, activity in activities.items():
        duration = actual_durations[activity_id]
        start_offset = earliest[activity_id]
        finish_display_offset = start_offset if duration == 0 else start_offset + duration - 1
        activity["calculated_start"] = workday_at(start, start_offset, weekdays, holidays).isoformat()
        activity["calculated_finish"] = workday_at(start, finish_display_offset, weekdays, holidays).isoformat()
        activity["total_float_workdays"] = latest[activity_id] - earliest[activity_id]
        activity["is_critical"] = latest[activity_id] == earliest[activity_id]
    plan["calculation_status"] = "complete"
    plan["calculation_method"] = "CPM on declared FS/SS/FF/SF relations and the declared working calendar"
    plan["calculated_finish"] = workday_at(start, max(0, finish_offset - 1), weekdays, holidays).isoformat()


def validate_and_enrich(records: dict[str, list[dict]], context: dict[str, set[str]]) -> tuple[dict[str, list[dict]], list[str]]:
    enriched = deepcopy(records)
    errors: list[str] = []
    maps: dict[str, dict[str, dict]] = {}
    for key, (_, id_field) in REGISTRIES.items():
        values = enriched.get(key, [])
        if not isinstance(values, list):
            errors.append(f"{key} must be an array")
            values = []
            enriched[key] = values
        maps[key] = ensure_unique(values, id_field, key, errors)
    for plan in enriched["cost_plans"]:
        enrich_cost_plan(plan, context, errors)
    for plan in enriched["schedule_plans"]:
        enrich_schedule_plan(plan, context, errors)
    for key, id_field, predecessor_field in (
        ("cost_plans", "cost_plan_id", "supersedes_cost_plan_id"),
        ("schedule_plans", "schedule_plan_id", "supersedes_schedule_plan_id"),
    ):
        seen_revisions: set[tuple[str, int]] = set()
        for identifier, plan in maps[key].items():
            series = text(plan.get("plan_series_id"))
            revision = plan.get("revision")
            if isinstance(revision, int) and not isinstance(revision, bool) and series:
                revision_key = (series, revision)
                if revision_key in seen_revisions:
                    errors.append(f"{key} duplicates series {series} revision {revision}")
                seen_revisions.add(revision_key)
                predecessor_id = text(plan.get(predecessor_field))
                if revision == 1 and predecessor_id:
                    errors.append(f"{key} {identifier} revision 1 must not supersede another plan")
                if revision > 1:
                    predecessor = maps[key].get(predecessor_id)
                    if (
                        not predecessor
                        or predecessor.get("plan_series_id") != series
                        or predecessor.get("revision") != revision - 1
                    ):
                        errors.append(f"{key} {identifier} lacks the immediately preceding revision")
    seen_baseline_versions: set[int] = set()
    for baseline_id, baseline in maps["management_baselines"].items():
        location = f"ManagementBaseline {baseline_id}"
        if baseline.get("status") not in BASELINE_STATUSES:
            errors.append(f"{location} has unknown status")
        iso_datetime(baseline.get("accepted_at"), f"{location}.accepted_at", errors)
        version = baseline.get("baseline_version")
        if not isinstance(version, int) or isinstance(version, bool) or version < 1:
            errors.append(f"{location}.baseline_version must be a positive integer")
        elif version in seen_baseline_versions:
            errors.append(f"management_baselines duplicates baseline_version {version}")
        else:
            seen_baseline_versions.add(version)
        cost = maps["cost_plans"].get(text(baseline.get("cost_plan_id")))
        schedule = maps["schedule_plans"].get(text(baseline.get("schedule_plan_id")))
        if not cost or cost.get("status") != "ready_for_baseline":
            errors.append(f"{location} requires a ready CostPlan")
        if not schedule or schedule.get("status") != "ready_for_baseline" or schedule.get("calculation_status") != "complete":
            errors.append(f"{location} requires a calculated ready SchedulePlan")
        if cost and schedule and cost.get("baseline_snapshot_id") != schedule.get("baseline_snapshot_id"):
            errors.append(f"{location} cost and schedule plans use different project baselines")
        if cost and schedule:
            cost_work_items = {
                text(value.get("work_item_id"))
                for value in cost.get("items", [])
                if isinstance(value, dict) and text(value.get("work_item_id"))
            }
            schedule_work_items = {
                text(value.get("work_item_id"))
                for value in schedule.get("activities", [])
                if isinstance(value, dict) and text(value.get("work_item_id"))
            }
            if cost_work_items != schedule_work_items:
                errors.append(f"{location} cost and schedule plans cover different work_item_id sets")
        decision_id = text(baseline.get("owner_decision_id"))
        if decision_id not in context["decisions"]:
            errors.append(f"{location} requires a registered OwnerDecision")
        predecessor = text(baseline.get("supersedes_management_baseline_id"))
        if version == 1 and predecessor:
            errors.append(f"{location} version 1 must not supersede another baseline")
        if isinstance(version, int) and version > 1:
            prior = maps["management_baselines"].get(predecessor)
            if not prior or prior.get("baseline_version") != version - 1:
                errors.append(f"{location} version {version} requires the immediately preceding baseline")
    for assessment_id, assessment in maps["change_impact_assessments"].items():
        location = f"ChangeImpactAssessment {assessment_id}"
        status = assessment.get("status")
        if status not in CHANGE_STATUSES:
            errors.append(f"{location} has unknown status")
        baseline = maps["management_baselines"].get(text(assessment.get("management_baseline_id")))
        if not baseline:
            errors.append(f"{location} requires a known ManagementBaseline")
        change_id = text(assessment.get("change_id"))
        if change_id not in context["changes"]:
            errors.append(f"{location} requires a known changes.csv change_id")
        cost_delta = decimal_value(assessment.get("cost_delta"), f"{location}.cost_delta", errors, allow_none=True)
        schedule_delta = assessment.get("schedule_delta_workdays")
        if schedule_delta is not None and (not isinstance(schedule_delta, int) or isinstance(schedule_delta, bool)):
            errors.append(f"{location}.schedule_delta_workdays must be an integer or null")
        if status == "approved":
            if cost_delta is None or schedule_delta is None:
                errors.append(f"{location} approved impact requires explicit cost and schedule deltas")
            if text(assessment.get("decision_id")) not in context["decisions"]:
                errors.append(f"{location} approved impact requires a registered OwnerDecision")
            if change_id not in context.get("approved_changes", set()):
                errors.append(f"{location} cannot be approved while changes.csv is not approved")
            iso_date(assessment.get("effective_date"), f"{location}.effective_date", errors)
        if baseline:
            cost_plan = maps["cost_plans"].get(text(baseline.get("cost_plan_id")), {})
            schedule_plan = maps["schedule_plans"].get(text(baseline.get("schedule_plan_id")), {})
            cost_item_ids = {
                text(value.get("item_id")) for value in cost_plan.get("items", []) if isinstance(value, dict)
            }
            activity_ids = {
                text(value.get("activity_id")) for value in schedule_plan.get("activities", []) if isinstance(value, dict)
            }
            affected_cost = string_list(assessment.get("affected_cost_item_ids", []), f"{location}.affected_cost_item_ids", errors)
            affected_schedule = string_list(assessment.get("affected_activity_ids", []), f"{location}.affected_activity_ids", errors)
            if any(value not in cost_item_ids for value in affected_cost):
                errors.append(f"{location} contains cost items outside the linked baseline")
            if any(value not in activity_ids for value in affected_schedule):
                errors.append(f"{location} contains activities outside the linked baseline")
            if text(assessment.get("currency")) != text(cost_plan.get("currency")):
                errors.append(f"{location}.currency must match the linked CostPlan")
        require_sources(assessment.get("source_ids", []), f"{location}.source_ids", context["sources"], errors, status == "approved")
    for snapshot_id, snapshot in maps["control_snapshots"].items():
        location = f"ControlSnapshot {snapshot_id}"
        complete = snapshot.get("status") == "complete"
        if snapshot.get("status") not in CONTROL_STATUSES:
            errors.append(f"{location} has unknown status")
        data_date = iso_date(snapshot.get("data_date"), f"{location}.data_date", errors)
        baseline = maps["management_baselines"].get(text(snapshot.get("management_baseline_id")))
        if not baseline:
            errors.append(f"{location} requires a known ManagementBaseline")
            continue
        cost = maps["cost_plans"].get(text(baseline.get("cost_plan_id")))
        schedule = maps["schedule_plans"].get(text(baseline.get("schedule_plan_id")))
        if not cost or not schedule:
            continue
        impact_ids = string_list(snapshot.get("change_impact_assessment_ids", []), f"{location}.change_impact_assessment_ids", errors)
        impacts = [maps["change_impact_assessments"].get(value) for value in impact_ids]
        if any(value is None for value in impacts):
            errors.append(f"{location} contains unknown change impact links")
        if any(value and value.get("management_baseline_id") != snapshot.get("management_baseline_id") for value in impacts):
            errors.append(f"{location} contains a change impact from another baseline")
        if data_date:
            expected_impacts = {
                identifier
                for identifier, value in maps["change_impact_assessments"].items()
                if value.get("management_baseline_id") == snapshot.get("management_baseline_id")
                and value.get("status") == "approved"
                and (iso_date(value.get("effective_date"), f"{location}.approved_change_effective_date", errors) or date.max) <= data_date
            }
            if complete and set(impact_ids) != expected_impacts:
                errors.append(f"{location} complete snapshot must include every approved change effective by data_date")
        approved_delta = sum(
            (Decimal(str(value.get("cost_delta"))) for value in impacts if value and value.get("status") == "approved"),
            Decimal("0"),
        )
        currency = text(cost.get("currency"))
        if text(snapshot.get("currency")) != currency:
            errors.append(f"{location}.currency must match its CostPlan")
        actual = decimal_value(snapshot.get("confirmed_actual_cost"), f"{location}.confirmed_actual_cost", errors)
        actual_cost_ids = string_list(
            snapshot.get("confirmed_actual_cost_ids", []),
            f"{location}.confirmed_actual_cost_ids",
            errors,
        )
        scoped_work_items = {
            text(value.get("work_item_id"))
            for value in cost.get("items", [])
            if isinstance(value, dict) and text(value.get("work_item_id"))
        }
        recomputed_actual = Decimal("0")
        for cost_id in actual_cost_ids:
            row = context.get("cost_rows", {}).get(cost_id)
            if not row:
                errors.append(f"{location} has unknown confirmed actual cost ID {cost_id}")
                continue
            row_date = iso_date(row.get("date"), f"{location}.cost[{cost_id}].date", errors)
            if (
                row.get("status", "").strip() != "confirmed_paid"
                or row.get("currency", "").strip() != currency
                or row.get("work_item_id", "").strip() not in scoped_work_items
                or (row_date and data_date and row_date > data_date)
                or row.get("evidence_document_id", "").strip() not in context.get("active_documents", set())
                or not row.get("evidence_locator", "").strip()
            ):
                errors.append(f"{location} cost {cost_id} is not a valid in-scope confirmed payment at data_date")
                continue
            amount = decimal_value(row.get("amount"), f"{location}.cost[{cost_id}].amount", errors)
            if amount is not None:
                recomputed_actual += amount
        if actual is not None and abs(actual - recomputed_actual) > TOLERANCE:
            errors.append(f"{location}.confirmed_actual_cost differs from linked cost rows")
        remaining = decimal_value(snapshot.get("estimate_to_complete"), f"{location}.estimate_to_complete", errors, allow_none=not complete)
        forecast_finish = iso_date(snapshot.get("forecast_finish"), f"{location}.forecast_finish", errors, allow_empty=not complete)
        baseline_finish = iso_date(schedule.get("calculated_finish"), f"{location}.baseline_finish", errors)
        measurements = snapshot.get("progress_measurements", [])
        if not isinstance(measurements, list) or (complete and not measurements):
            errors.append(f"{location}.progress_measurements must be a non-empty array for a complete snapshot")
            measurements = []
        activity_map = {text(value.get("activity_id")): value for value in schedule.get("activities", [])}
        seen: set[str] = set()
        weight_total = Decimal("0")
        actual_weighted = Decimal("0")
        planned_weighted = Decimal("0")
        schedule_start = iso_date(schedule.get("project_start"), f"{location}.schedule_start", errors)
        weekdays, holidays = working_calendar(schedule, location, errors)
        cutoff = workday_index(schedule_start, data_date, weekdays, holidays) if schedule_start and data_date else -1
        for number, measurement in enumerate(measurements, 1):
            item_location = f"{location}.progress_measurements[{number}]"
            if not isinstance(measurement, dict):
                errors.append(f"{item_location} must be an object")
                continue
            activity_id = text(measurement.get("activity_id"))
            activity = activity_map.get(activity_id)
            if not activity or activity_id in seen:
                errors.append(f"{item_location} has unknown or duplicate activity_id")
                continue
            seen.add(activity_id)
            weight = decimal_value(measurement.get("weight"), f"{item_location}.weight", errors)
            progress = decimal_value(measurement.get("physical_progress_percent"), f"{item_location}.physical_progress_percent", errors)
            if weight is None or progress is None:
                continue
            if weight < 0 or progress < 0 or progress > 100:
                errors.append(f"{item_location} has weight or progress outside its allowed range")
                continue
            weight_total += weight
            actual_weighted += weight * progress
            start_date = iso_date(activity.get("calculated_start"), f"{item_location}.calculated_start", errors)
            finish_date = iso_date(activity.get("calculated_finish"), f"{item_location}.calculated_finish", errors)
            if data_date and start_date and finish_date:
                if data_date < start_date:
                    planned = Decimal("0")
                elif data_date >= finish_date:
                    planned = Decimal("100")
                else:
                    start_index = workday_index(schedule_start, start_date, weekdays, holidays) if schedule_start else 0
                    duration = max(1, int(activity.get("duration_workdays", 0)))
                    elapsed = max(0, cutoff - start_index + 1)
                    planned = min(Decimal("100"), Decimal(elapsed * 100) / Decimal(duration))
                planned_weighted += weight * planned
            require_sources(measurement.get("source_ids", []), f"{item_location}.source_ids", context["sources"], errors, complete)
        if complete and (abs(weight_total - Decimal("1")) > Decimal("0.000001") or seen != set(activity_map)):
            errors.append(f"{location} complete progress must cover every activity with weights summing to 1")
        baseline_budget = Decimal(str(cost.get("total_amount", 0)))
        current_budget = baseline_budget + approved_delta
        actual_progress = actual_weighted / weight_total if weight_total else None
        planned_progress = planned_weighted / weight_total if weight_total else None
        eac = actual + remaining if actual is not None and remaining is not None else None
        earned_value = current_budget * actual_progress / Decimal("100") if actual_progress is not None else None
        planned_value = current_budget * planned_progress / Decimal("100") if planned_progress is not None else None
        snapshot["metrics"] = {
            "baseline_budget": float(baseline_budget),
            "approved_change_delta": float(approved_delta),
            "current_budget": float(current_budget),
            "confirmed_actual_cost": float(actual) if actual is not None else None,
            "estimate_to_complete": float(remaining) if remaining is not None else None,
            "forecast_at_completion": float(eac) if eac is not None else None,
            "cost_variance_at_completion": float(current_budget - eac) if eac is not None else None,
            "planned_progress_percent": float(planned_progress) if planned_progress is not None else None,
            "actual_progress_percent": float(actual_progress) if actual_progress is not None else None,
            "planned_value": float(planned_value) if planned_value is not None else None,
            "earned_value": float(earned_value) if earned_value is not None else None,
            "cost_performance_index": float(earned_value / actual) if earned_value is not None and actual else None,
            "schedule_performance_index": float(earned_value / planned_value) if earned_value is not None and planned_value else None,
            "baseline_finish": baseline_finish.isoformat() if baseline_finish else None,
            "forecast_finish": forecast_finish.isoformat() if forecast_finish else None,
            "schedule_variance_calendar_days": (forecast_finish - baseline_finish).days if forecast_finish and baseline_finish else None,
        }
        snapshot["formula_notes"] = [
            "current_budget = baseline_budget + approved_change_delta",
            "forecast_at_completion = confirmed_actual_cost + estimate_to_complete",
            "cost_variance_at_completion = current_budget - forecast_at_completion",
            "PV and EV use the explicit activity weights recorded in this snapshot",
        ]
        require_sources(snapshot.get("source_ids", []), f"{location}.source_ids", context["sources"], errors, complete)
    return enriched, errors
