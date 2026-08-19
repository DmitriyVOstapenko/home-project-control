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

    quote = quotes.get(str(review.get("quote_id", "")), {})
    quote_items = [item for item in items.values() if item.get("quote_id") == review.get("quote_id")]
    review_findings = [findings[value] for value in review.get("finding_ids", []) if value in findings]
    review_alternatives = [
        alternatives[value] for value in review.get("alternative_ids", []) if value in alternatives
    ]
    blockers = review.get("essential_blockers", []) if isinstance(review.get("essential_blockers"), list) else []
    questions = review.get("contractor_questions", []) if isinstance(review.get("contractor_questions"), list) else []
    disciplines = review.get("disciplines", []) if isinstance(review.get("disciplines"), list) else []

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
    owner = f"""# Карточка решения по КП {review_id}

## Статус

- Состояние проверки: `{review.get('status', 'unknown')}`
- Документ: `{review.get('source_document_id', '')}`, версия `{review.get('document_version', '')}`
- Направления: {', '.join(str(value) for value in disciplines) or 'не указаны'}
- Коммерческая запись: `{review.get('quote_id', '')}`

## Существенные препятствия для решения

{bullets(blockers)}

## Незавершённые пункты обязательного контракта

{bullets(incomplete_contract)}

## Что в предложении подтверждено хорошо

{bullets(positive)}

## Основные замечания и риски

{bullets(risks)}

## Возможные варианты

{bullets([value.get('description', value.get('title', value.get('alternative_id'))) for value in review_alternatives])}

## Проверенные технические подходы

{bullets(technical_options)}

Эта карточка не заменяет решение владельца и обязательную проверку профильным специалистом там, где она требуется.
"""

    request = f"""# Запрос подрядчику по КП {review.get('quote_id', '')}

Просим предоставить уточнения и документы к редакции предложения, связанной с документом `{review.get('source_document_id', '')}` версии `{review.get('document_version', '')}`.

## Вопросы и недостающие данные

{bullets(questions)}

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
        f"кандидаты: {', '.join(value.get('candidate_contractor_ids', [])) or 'нет'}"
        for value in review.get("search_runs", [])
    ]
    dossier = f"""# Полное досье проверки КП {review_id}

## Объект проверки и прослеживаемость

- Документ: `{review.get('source_document_id', '')}`
- Версия: `{review.get('document_version', '')}`
- SHA-256: `{review.get('sha256', '')}`
- Инвентаризация: `{review.get('inventory_id', '')}`
- Журналы чтения: {', '.join(review.get('reading_run_ids', []))}
- Направления: {', '.join(str(value) for value in disciplines)}

## Строки предложения

{bullets(item_lines)}

## Матрица утверждённых требований и КП

{bullets(matrix_lines)}

Непривязанные строки КП: {', '.join(review.get('unmatched_quote_item_ids', [])) or 'нет'}.

## Профессиональные технические проверки

{bullets(check_lines)}

## Обязательный универсальный контракт

{bullets(mandatory_lines)}

## Обязательные проверки по направлениям

{bullets(discipline_lines)}

## Альтернативные технические решения

{bullets(technical_alternative_lines)}

## Дополнительный анализ модели

{bullets(additional_lines)}

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
