#!/usr/bin/env python3
"""Preview or create three source-linked Markdown reports for one ProposalReview."""

from __future__ import annotations

import argparse
import json
import re
import sys
import uuid
from pathlib import Path


SCRIPT_DIR = Path(__file__).resolve().parent
MANAGE_SCRIPTS = SCRIPT_DIR.parents[1] / "manage-project-evidence" / "scripts"
sys.path.insert(0, str(MANAGE_SCRIPTS))

from inspect_project import is_linklike, require_ready_project  # noqa: E402


def read_jsonl(path: Path, id_field: str) -> dict[str, dict]:
    result: dict[str, dict] = {}
    for line_number, raw in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if not raw.strip():
            continue
        value = json.loads(raw)
        if not isinstance(value, dict):
            raise ValueError(f"{path.name}:{line_number}: expected a JSON object")
        identifier = value.get(id_field)
        if not isinstance(identifier, str) or not identifier.strip():
            raise ValueError(f"{path.name}:{line_number}: missing {id_field}")
        result[identifier] = value
    return result


def bullets(values: list[object], empty: str = "Нет зарегистрированных данных.") -> str:
    cleaned = [str(value).strip() for value in values if value is not None and str(value).strip()]
    return "\n".join(f"- {value}" for value in cleaned) if cleaned else empty


def source_label(record: dict) -> str:
    locator = str(record.get("locator", "")).strip()
    source = str(record.get("source_document_id", "")).strip()
    linked = record.get("source_ids", [])
    linked_text = ", ".join(str(value) for value in linked) if isinstance(linked, list) else ""
    base = f"{source}, {locator}" if source and locator else source or locator
    return base or linked_text or "источник не указан"


def amount_label(value: object, currency: str) -> str:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return "не подтверждено"
    return f"{value:,.2f} {currency}".replace(",", " ").replace(".00 ", " ")


def build_reports(root: Path, review_id: str) -> dict[str, str]:
    control = root / ".home-control"
    reviews = read_jsonl(control / "proposal_reviews.jsonl", "proposal_review_id")
    review = reviews.get(review_id)
    if review is None:
        raise ValueError(f"Unknown proposal_review_id: {review_id}")
    quotes = read_jsonl(control / "quotes.jsonl", "quote_id")
    items = read_jsonl(control / "quote_items.jsonl", "quote_item_id")
    requirements = read_jsonl(control / "approved_requirements.jsonl", "requirement_id")
    findings = read_jsonl(control / "findings.jsonl", "finding_id")
    alternatives = read_jsonl(control / "alternatives.jsonl", "alternative_id")
    compliance_assessments = read_jsonl(
        control / "compliance_assessments.jsonl", "compliance_assessment_id"
    )
    compliance_results = read_jsonl(control / "compliance_results.jsonl", "compliance_result_id")
    regulatory_requirements = read_jsonl(
        control / "regulatory_requirements.jsonl", "regulatory_requirement_id"
    )
    norm_references = read_jsonl(control / "norm_references.jsonl", "norm_reference_id")

    quote = quotes.get(str(review.get("quote_id", "")), {})
    quote_items = [item for item in items.values() if item.get("quote_id") == review.get("quote_id")]
    review_findings = [findings[value] for value in review.get("finding_ids", []) if value in findings]
    review_alternatives = [
        alternatives[value] for value in review.get("alternative_ids", []) if value in alternatives
    ]
    blockers = review.get("essential_blockers", []) if isinstance(review.get("essential_blockers"), list) else []
    questions = review.get("contractor_questions", []) if isinstance(review.get("contractor_questions"), list) else []
    disciplines = review.get("disciplines", []) if isinstance(review.get("disciplines"), list) else []
    foreman = review.get("foreman_assessment", {}) if isinstance(review.get("foreman_assessment"), dict) else {}
    cost = review.get("cost_exposure", {}) if isinstance(review.get("cost_exposure"), dict) else {}
    currency = str(cost.get("currency", quote.get("currency", ""))).strip()
    scope_rows = review.get("scope_boundary_matrix", []) if isinstance(review.get("scope_boundary_matrix"), list) else []
    constructability = review.get("constructability_walkthrough", []) if isinstance(review.get("constructability_walkthrough"), list) else []
    contractor_assessment = review.get("contractor_assessment", []) if isinstance(review.get("contractor_assessment"), list) else []
    site_plan = review.get("site_verification_plan", []) if isinstance(review.get("site_verification_plan"), list) else []
    acceptance_plan = review.get("acceptance_plan", []) if isinstance(review.get("acceptance_plan"), list) else []
    priority_risks = review.get("priority_risks", []) if isinstance(review.get("priority_risks"), list) else []
    baseline_mode = str(review.get("baseline_assessment_mode", "legacy_unspecified")).strip()
    baseline_snapshot_id = str(review.get("baseline_snapshot_id", "")).strip()
    baseline_applicability_scope = str(review.get("baseline_applicability_scope", "")).strip()
    baseline_limitations = (
        review.get("baseline_limitations", []) if isinstance(review.get("baseline_limitations"), list) else []
    )
    reference_comparisons = (
        review.get("reference_comparisons", []) if isinstance(review.get("reference_comparisons"), list) else []
    )
    review_compliance = [
        compliance_assessments[value]
        for value in review.get("compliance_assessment_ids", [])
        if value in compliance_assessments
    ]
    normative_check = next(
        (
            value
            for value in review.get("mandatory_checks", [])
            if isinstance(value, dict) and value.get("check_id") == "norms_and_specialist_boundary"
        ),
        {},
    )
    regulatory_summary = (
        "; ".join(
            f"{value.get('compliance_assessment_id', '')}: {value.get('status', '')}"
            for value in review_compliance
        )
        if review_compliance
        else f"{normative_check.get('status', 'не выполнена')}: {normative_check.get('result', '')}"
    )

    positive = [
        f"{item.get('statement', item.get('description', item.get('finding_id')))} ({source_label(item)})"
        for item in review_findings
        if item.get("severity") in {"positive", "strength"} or item.get("finding_type") == "strength"
    ]
    risks = [
        f"{item.get('statement', item.get('description', item.get('finding_id')))} ({source_label(item)})"
        for item in review_findings
        if not (item.get("severity") in {"positive", "strength"} or item.get("finding_type") == "strength")
    ]
    incomplete_contract = [
        f"{value.get('check_id', value.get('axis_id', value.get('track_id', 'без ID')))}: "
        f"{value.get('status', '')} — {value.get('result', '')}"
        for field in ("mandatory_checks", "discipline_checks", "technical_alternative_assessments")
        for value in review.get(field, [])
        if value.get("status") not in {"completed", "not_applicable"}
    ]
    technical_options = [
        f"{value.get('track_id', '')} [{value.get('status', '')}]: "
        f"{value.get('solution', value.get('result', ''))}; применимость: {value.get('project_fit', value.get('rationale', ''))}"
        for value in review.get("technical_alternative_assessments", [])
    ]
    scope_gaps = [
        f"{row.get('scope_id', 'без ID')}: {gap}"
        for row in scope_rows
        if isinstance(row, dict)
        for gap in row.get("gaps", [])
        if str(gap).strip()
    ]
    risk_lines = [
        f"{risk.get('risk_id', 'без ID')} -> {risk.get('finding_id', 'без источника')}; "
        f"{risk.get('urgency', 'без срока')} / {', '.join(risk.get('impact_lanes', []))}: "
        f"{risk.get('consequence', '')}; действие: {risk.get('owner_action', '')}"
        for risk in priority_risks
        if isinstance(risk, dict)
    ]
    open_site_checks = [
        f"{item.get('verification_id', 'без ID')} [{item.get('status', '')}]: {item.get('subject', '')}; "
        f"до: {item.get('required_before', '')}"
        for item in site_plan
        if isinstance(item, dict) and item.get("status") not in {"completed", "not_applicable"}
    ]
    unknown_costs = [
        f"{item.get('description', '')}: {item.get('reason', '')}"
        + (" (блокирует договор)" if item.get("blocking") is True else "")
        for item in cost.get("unknown_exposures", [])
        if isinstance(item, dict)
    ]
    preferred_alternative_id = str(foreman.get("preferred_alternative_id", "")).strip()
    preferred_alternative = alternatives.get(preferred_alternative_id, {})
    preferred_label = (
        f"`{preferred_alternative_id}` — "
        f"{preferred_alternative.get('description', preferred_alternative.get('title', 'описание отсутствует'))}"
        if preferred_alternative_id
        else "доказанное предпочтение не выбрано"
    )
    conditions = [
        *(f"До договора: {value}" for value in foreman.get("conditions_before_contract", [])),
        *(f"До работ: {value}" for value in foreman.get("conditions_before_work", [])),
    ]
    evidence_coverage = (
        "обязательные проверки закрыты"
        if not incomplete_contract
        else f"не закрыто пунктов: {len(incomplete_contract)}"
    )
    unresolved = [*blockers, *baseline_limitations, *unknown_costs, *open_site_checks]
    executive_summary = f"""## Решение владельца — кратко

- **Вердикт и готовность:** `{foreman.get('verdict', 'не сформирован')}`; `{foreman.get('decision_readiness', 'не определена')}`.
- **Что нужно решить:** {foreman.get('decision_request', 'не сформулировано')}.
- **Вывод:** {foreman.get('summary', 'не сформирован')}.
- **Цена:** КП {amount_label(cost.get('quoted_total'), currency)}; расчётный диапазон {amount_label(cost.get('estimated_total_low'), currency)} — {amount_label(cost.get('estimated_total_high'), currency)}.
- **Предпочтительный вариант:** {preferred_label}. Основание: {foreman.get('preferred_alternative_rationale', 'не сформировано')}.
- **Нормативная проверка:** {regulatory_summary}.
- **Покрытие доказательств:** {evidence_coverage}; базовый режим `{baseline_mode}`; открытых зависимых ограничений: {len(unresolved)}.

### Три главных риска

{bullets(risk_lines[:3])}

### Три обязательных условия

{bullets(conditions[:3])}

### Ближайшие действия

{bullets(foreman.get('owner_next_actions', [])[:3])}
"""
    owner = f"""# Карточка решения по КП {review_id}

{executive_summary}

Полное обоснование: [full-dossier.md](full-dossier.md). Карточка не заменяет решение владельца и обязательную проверку профильным специалистом там, где она требуется.
"""

    request = f"""# Запрос подрядчику по КП {review.get('quote_id', '')}

Просим предоставить уточнения и документы к редакции предложения, связанной с документом `{review.get('source_document_id', '')}` версии `{review.get('document_version', '')}`.

## Вопросы и недостающие данные

{bullets(questions)}

## Уточнения по границам объёма

{bullets(scope_gaps)}

## Данные для проверки на объекте

{bullets(open_site_checks)}

## Требования к ответу

- Дайте ответ по каждому пункту отдельно.
- Для изменения цены, количества, модели, состава или срока укажите новую редакцию позиции и причину изменения.
- Приложите расчёты, спецификации, паспорта и ссылки на применимые нормы там, где на них основано решение.
- Явно перечислите включения, исключения, работы заказчика, налоги, доставку, пусконаладку, документы сдачи и гарантию.
"""

    matrix_lines = []
    for match in review.get("requirement_matches", []):
        requirement = requirements.get(str(match.get("requirement_id", "")), {})
        matrix_lines.append(
            f"`{match.get('requirement_id', '')}` — {requirement.get('requirement', requirement.get('statement', ''))}; "
            f"статус `{match.get('status', '')}`; строки КП: {', '.join(match.get('quote_item_ids', [])) or 'нет'}"
        )
    reference_lines = [
        f"`{value.get('document_id', '')}` v{value.get('document_version', '')} "
        f"[{value.get('status', '')}] {value.get('statement', '')}; роль: {value.get('project_role', '')}; "
        f"область: {value.get('applicability_scope', '')}; локатор: {value.get('locator', '')}; "
        f"ограничение: {value.get('limitations', '')}; строки КП: "
        f"{', '.join(value.get('quote_item_ids', [])) or 'нет'}"
        for value in reference_comparisons
        if isinstance(value, dict)
    ]
    item_lines = [
        f"`{item.get('quote_item_id', '')}` — {item.get('raw_text', '')}; сумма: {item.get('amount', 'не указана')} "
        f"{item.get('currency', quote.get('currency', ''))}; локатор: {item.get('locator', '')}"
        for item in quote_items
    ]
    check_lines = [
        f"`{value.get('check_id', '')}` [{value.get('status', '')}] {value.get('category', '')}: "
        f"{value.get('criterion', '')}; источники: {', '.join(value.get('source_ids', [])) or 'нет'}"
        for value in review.get("technical_checks", [])
    ]
    mandatory_lines = [
        f"`{value.get('check_id', '')}` [{value.get('status', '')}] {value.get('result', '')}; "
        f"основание: {value.get('rationale', '')}; источники: {', '.join(value.get('source_ids', [])) or 'нет'}"
        for value in review.get("mandatory_checks", [])
    ]
    discipline_lines = [
        f"`{value.get('discipline', '')}|{value.get('axis_id', '')}` [{value.get('status', '')}] "
        f"{value.get('result', '')}; основание: {value.get('rationale', '')}; "
        f"источники: {', '.join(value.get('source_ids', [])) or 'нет'}"
        for value in review.get("discipline_checks", [])
    ]
    technical_alternative_lines = [
        f"`{value.get('track_id', '')}` [{value.get('status', '')}] {value.get('result', '')}; "
        f"решение: {value.get('solution', '')}; применимость: {value.get('project_fit', value.get('rationale', ''))}; "
        f"преимущества: {value.get('benefits', '')}; ограничения: {value.get('drawbacks', '')}; "
        f"влияние: {value.get('implementation_impacts', '')}; жизненный цикл: {value.get('lifecycle_cost_notes', '')}"
        for value in review.get("technical_alternative_assessments", [])
    ]
    additional_lines = [
        f"`{value.get('check_id', '')}` [{value.get('status', '')}] {value.get('question', '')}: "
        f"{value.get('result', '')}"
        for value in review.get("additional_model_checks", [])
    ]
    calculation_lines = [
        f"`{value.get('calculation_id', '')}` [{value.get('status', '')}] `{value.get('formula', '')}`; "
        f"результат: {value.get('result', 'не указан')} {value.get('unit', '')}"
        for value in review.get("calculations", [])
    ]
    search_lines = [
        f"`{value.get('search_run_id', '')}` [{value.get('status', '')}], {value.get('checked_at', '')}, "
        f"регион: {value.get('region', '')}; запросы: {', '.join(value.get('queries', []))}; "
        f"источники: {', '.join(value.get('source_urls', [])) or 'нет'}; "
        f"подрядчики: {', '.join(value.get('candidate_contractor_ids', [])) or 'нет'}; "
        f"поставщики: {', '.join(value.get('candidate_supplier_ids', [])) or 'нет'}; оценки: "
        + " | ".join(
            f"{candidate.get('counterparty_kind', '')}:{candidate.get('counterparty_id', '')} "
            f"[{candidate.get('comparability_status', '')}] — {candidate.get('basis', '')}; "
            f"не хватает: {', '.join(candidate.get('missing_information', [])) or 'ничего'}"
            for candidate in value.get("candidate_assessments", [])
            if isinstance(candidate, dict)
        )
        for value in review.get("search_runs", [])
    ]
    scope_lines = [
        f"`{row.get('scope_id', '')}`: {row.get('result', '')}; строки КП: "
        f"{', '.join(row.get('quote_item_ids', [])) or 'нет'}; требования: "
        f"{', '.join(row.get('requirement_ids', [])) or 'нет'}; пробелы: "
        f"{', '.join(row.get('gaps', [])) or 'нет'}; ответственность: "
        + "; ".join(f"{role}={party}" for role, party in row.get("responsibilities", {}).items())
        for row in scope_rows
        if isinstance(row, dict)
    ]
    constructability_lines = [
        f"`{item.get('phase_id', '')}` [{item.get('status', '')}]: {item.get('result', '')}; "
        f"риски: {', '.join(item.get('risks', [])) or 'нет'}; действия: {', '.join(item.get('actions', [])) or 'нет'}"
        for item in constructability
        if isinstance(item, dict)
    ]
    contractor_lines = [
        f"`{item.get('axis_id', '')}` [{item.get('status', '')}]: {item.get('result', '')}"
        for item in contractor_assessment
        if isinstance(item, dict)
    ]
    site_lines = [
        f"`{item.get('verification_id', '')}` [{item.get('status', '')}]: {item.get('subject', '')}; "
        f"метод: {item.get('method', '')}; ответственный: {item.get('responsible_role', '')}; "
        f"до: {item.get('required_before', '')}; последствие: {item.get('consequence_if_unverified', '')}"
        for item in site_plan
        if isinstance(item, dict)
    ]
    acceptance_lines = [
        f"`{item.get('acceptance_id', '')}`: {item.get('result', '')}; критерий: {item.get('criterion', '')}; "
        f"метод: {item.get('method', '')}; момент: {item.get('timing', '')}; "
        f"ответственный: {item.get('responsible_party', '')}; доказательства: "
        f"{', '.join(item.get('evidence_required', [])) or 'нет'}"
        for item in acceptance_plan
        if isinstance(item, dict)
    ]
    priority_risk_lines = [
        f"`{item.get('risk_id', '')}` -> `{item.get('finding_id', '')}` [{item.get('urgency', '')}; "
        f"{', '.join(item.get('impact_lanes', []))}]: {item.get('consequence', '')}; "
        f"снижение: {item.get('mitigation', '')}; действие владельца: {item.get('owner_action', '')}"
        for item in priority_risks
        if isinstance(item, dict)
    ]
    compliance_lines: list[str] = []
    for assessment in review_compliance:
        for result_id in assessment.get("result_ids", []):
            result = compliance_results.get(result_id, {})
            requirement = regulatory_requirements.get(str(result.get("requirement_id", "")), {})
            norm = norm_references.get(str(requirement.get("norm_reference_id", "")), {})
            compliance_lines.append(
                f"`{result_id}`: {norm.get('designation', 'норма не указана')} "
                f"{requirement.get('locator', '')} — применимость `{result.get('applicability_status', '')}`, "
                f"результат `{result.get('compliance_status', '')}`; {result.get('basis', '')}"
            )
    if baseline_mode == "accepted_baseline":
        baseline_matrix_section = f"""## Матрица принятой базовой линии и КП

{bullets(matrix_lines)}"""
    else:
        baseline_matrix_section = """## Соответствие принятой базовой линии

Не оценивалось: применяемая базовая линия не принята. Сопоставления с документами выше имеют только справочный статус."""
    dossier = f"""# Полное досье проверки КП {review_id}

{executive_summary}

## Объект проверки и прослеживаемость

- Документ: `{review.get('source_document_id', '')}`
- Версия: `{review.get('document_version', '')}`
- SHA-256: `{review.get('sha256', '')}`
- Инвентаризация: `{review.get('inventory_id', '')}`
- Журналы чтения: {', '.join(review.get('reading_run_ids', []))}
- Журналы извлечения фактов: {', '.join(review.get('fact_extraction_run_ids', []))}
- Пакеты проекта: {', '.join(review.get('project_package_ids', []))}
- Пробелы данных: {', '.join(review.get('information_gap_ids', [])) or 'нет'}
- Межпакетные коллизии: {', '.join(review.get('coordination_issue_ids', [])) or 'нет'}
- Нормативные оценки: {', '.join(review.get('compliance_assessment_ids', [])) or 'не зарегистрированы'}
- Направления: {', '.join(str(value) for value in disciplines)}
- Базовый режим: `{baseline_mode}`
- Снимок базовой линии: `{baseline_snapshot_id or 'не принят'}`
- Применённая область базы: {baseline_applicability_scope or 'не применяется'}

### Зависимые ограничения

{bullets(baseline_limitations)}

### Справочные сопоставления

{bullets(reference_lines)}

## Прорабский вывод

- Вердикт: `{foreman.get('verdict', 'не сформирован')}`
- Готовность: `{foreman.get('decision_readiness', 'не определена')}`
- Вывод: {foreman.get('summary', 'не сформирован')}

Решающие причины:

{bullets(foreman.get('decisive_reasons', []))}

## Строки предложения

{bullets(item_lines)}

{baseline_matrix_section}

Непривязанные строки КП: {', '.join(review.get('unmatched_quote_item_ids', [])) or 'нет'}.

## Нормативное соответствие

{bullets(compliance_lines, empty=regulatory_summary)}

## Профессиональные технические проверки

{bullets(check_lines)}

## Границы объёма и ответственности

{bullets(scope_lines)}

## Проход исполнимости

{bullets(constructability_lines)}

## Полная денежная экспозиция

- Заявлено: {amount_label(cost.get('quoted_total'), currency)}
- Подтверждённо включено: {amount_label(cost.get('confirmed_included_amount'), currency)}
- Известно исключено: {amount_label(cost.get('known_excluded_amount'), currency)}
- Диапазон: {amount_label(cost.get('estimated_total_low'), currency)} — {amount_label(cost.get('estimated_total_high'), currency)}
- Формула: `{cost.get('formula', '')}`
- Статус: `{cost.get('status', '')}`

Неизвестная экспозиция:

{bullets(unknown_costs)}

## Проверка подрядчика или поставщика

{bullets(contractor_lines)}

## План проверки на объекте

{bullets(site_lines)}

## План приёмки

{bullets(acceptance_lines)}

## Приоритетные риски

{bullets(priority_risk_lines)}

Сводка: {review.get('risk_summary', 'не сформирована')}

## Обязательный универсальный контракт

{bullets(mandatory_lines)}

## Обязательные проверки по направлениям

{bullets(discipline_lines)}

## Альтернативные технические решения

{bullets(technical_alternative_lines)}

## Дополнительный анализ модели

{bullets(additional_lines)}

Сводка дополнительного анализа: {review.get('additional_analysis_summary', 'не сформирована')}

## Проверяемые расчёты

{bullets(calculation_lines)}

## Замечания

{bullets([f"{value.get('finding_id', '')}: {value.get('statement', value.get('description', ''))} ({source_label(value)})" for value in review_findings])}

## Альтернативы

{bullets([f"{value.get('alternative_id', '')}: {value.get('description', value.get('title', ''))}" for value in review_alternatives])}

## Внешний поиск и кандидаты

{bullets(search_lines)}

## Недостающие данные и вопросы подрядчику

{bullets(blockers)}

{bullets(questions)}

## Условия и следующие действия

До договора:

{bullets(foreman.get('conditions_before_contract', []))}

До начала работ:

{bullets(foreman.get('conditions_before_work', []))}

Действия владельца:

{bullets(foreman.get('owner_next_actions', []))}

## Ограничение вывода

Досье воспроизводит зарегистрированные данные и связи. Оно не подтверждает соответствие нормам, безопасность или техническую достаточность без требуемых расчётов, исходных данных и профильной проверки.
"""
    return {
        "owner-card.md": owner,
        "contractor-request.md": request,
        "full-dossier.md": dossier,
    }


def write_atomic(path: Path, text: str) -> None:
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    try:
        temporary.write_text(text.rstrip() + "\n", encoding="utf-8")
        temporary.replace(path)
    finally:
        if temporary.exists():
            temporary.unlink()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("project_dir", type=Path)
    parser.add_argument("proposal_review_id")
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()

    root = require_ready_project(args.project_dir)
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}", args.proposal_review_id):
        raise ValueError("proposal_review_id is not a safe path identifier")
    reports = build_reports(root, args.proposal_review_id)
    proposals_root = root / ".home-control" / "reports" / "proposals"
    target = proposals_root / args.proposal_review_id
    if target.exists() or is_linklike(target):
        raise ValueError("Refusing to overwrite an existing or unsafe proposal report target")
    if is_linklike(proposals_root) or not proposals_root.is_dir():
        raise ValueError("Unsafe proposal report target")
    paths = [target / name for name in reports]
    if any(is_linklike(path) for path in paths):
        raise ValueError("Unsafe linked proposal report path")
    result: dict[str, object] = {"mode": "preview", "would_create": [str(path) for path in paths]}
    if args.apply:
        temporary = proposals_root / f".{args.proposal_review_id}.{uuid.uuid4().hex}.tmp"
        temporary.mkdir()
        try:
            for name, text in reports.items():
                write_atomic(temporary / name, text)
            temporary.replace(target)
        finally:
            if temporary.exists():
                for name in reports:
                    candidate = temporary / name
                    if candidate.is_file() and not is_linklike(candidate):
                        candidate.unlink()
                temporary.rmdir()
        result = {"mode": "applied", "created": [str(path) for path in paths]}
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (OSError, UnicodeError, ValueError, json.JSONDecodeError) as exc:
        print(f"Proposal dossier failed: {exc}", file=sys.stderr)
        raise SystemExit(2)
