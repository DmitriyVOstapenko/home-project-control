# Пакет записи профессиональной проверки

Утилита `scripts/record_proposal_review.py` принимает JSON-пакет версии `1.0`. По умолчанию она только показывает план добавления; `--apply` разрешён после подтверждения пользователя. Записи добавляются только в управляемые реестры и проходят аудит до окончательного сохранения.

Допустимые разделы: `reading_runs`, `facts`, `contractors`, `suppliers`, `quotes`, `quote_items`, `equipment_options`, `price_observations`, `norm_references`, `findings`, `alternatives`, `proposal_reviews`.

Для `price_observations` обязательны `observed_at`, `region`, `currency`, `tax_context`, `delivery_context`, `availability_context` и прямой `source_url`. Для `norm_references` обязательны точные `title`, `version`, `territory`, `checked_at`, `locator`, `source_url` и область применения `scope`.

Минимальная форма центральной записи:

```json
{
  "proposal_review_id": "PR-0001",
  "source_document_id": "DOC-0001",
  "document_version": 1,
  "sha256": "...",
  "quote_id": "Q-0001",
  "status": "in_progress",
  "disciplines": ["electrical", "equipment_supply"],
  "inventory_id": "DI-...",
  "reading_run_ids": ["RR-0001"],
  "baseline_requirement_ids": ["AR-0001"],
  "requirement_matches": [
    {"requirement_id": "AR-0001", "status": "partial", "quote_item_ids": ["QI-0001"], "notes": "..."}
  ],
  "unmatched_quote_item_ids": ["QI-0002"],
  "technical_checks": [
    {"check_id": "TC-001", "category": "interfaces", "criterion": "...", "status": "partial", "source_ids": ["QI-0001", "AR-0001"], "notes": "..."}
  ],
  "calculations": [
    {"calculation_id": "CALC-001", "formula": "quantity * unit_price", "inputs": [{"name": "quantity", "value": 2, "unit": "pcs", "source_ids": ["QI-0001"]}], "result": 2000, "unit": "RUB", "status": "verified"}
  ],
  "search_runs": [
    {"search_run_id": "SR-001", "status": "partial", "queries": ["..."], "checked_at": "2026-08-19", "region": "...", "source_urls": [], "candidate_contractor_ids": [], "privacy_review": {"unnecessary_private_data_removed": true}}
  ],
  "finding_ids": ["FN-0001"],
  "alternative_ids": ["ALT-0001"],
  "essential_blockers": ["Нужен расчёт ..."],
  "contractor_questions": ["Предоставьте ..."],
  "mandatory_checks": [],
  "discipline_checks": [],
  "technical_alternative_assessments": [],
  "additional_model_checks": [],
  "completion_manifest": {}
}
```

Пустые массивы в примере обозначают разделы, которые нужно заполнить; с таким содержимым пакет не пройдёт проверку. Точный набор идентификаторов берётся из `schemas/proposal-review-contract.json`, а правила заполнения описаны в [обязательном контракте](mandatory-review-contract.md).

## Обязательные слои

- `mandatory_checks` содержит каждый универсальный `check_id` текущего контракта ровно один раз.
- `discipline_checks` содержит каждую комбинацию выбранного `discipline` и обязательного `axis_id` ровно один раз.
- `technical_alternative_assessments` содержит каждое обязательное направление технической альтернативы ровно один раз. Для `completed` нужны описание решения, применимость к проекту, преимущества, ограничения, влияние на исполнение, жизненный цикл, источники и при наличии связанные `Alternative`.
- `additional_model_checks` — открытый массив новых проверок, не перечисленных в обязательном контракте. Он не ограничен известным перечнем идентификаторов.
- `completion_manifest` фиксирует версию контракта и точные списки всех фактически записанных проверок.

Для каждого обязательного элемента допустимы `completed`, `not_applicable`, `blocked` и `requires_specialist`. Выполненный пункт содержит результат и источники; остальные состояния содержат результат и конкретное основание. `ready_for_owner` допускает только `completed` и `not_applicable` во всех обязательных слоях.

`disciplines` — открытый список применённых профилей, а не классификатор одного направления. В одном анализе допустимы несколько значений.

`ready_for_owner` требует полной инвентаризации и чтения точной версии, полного покрытия требований и строк КП, отсутствия существенных блокеров, зарегистрированного внешнего поиска и совпадающего `completion_manifest`. Это состояние не является решением владельца.
