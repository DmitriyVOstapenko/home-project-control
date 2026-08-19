# Пакет записи профессиональной проверки

Утилита `scripts/record_proposal_review.py` принимает транспортный JSON-пакет `schema_version: 1.0`. Эта версия относится к оболочке пакета и не равна версии обязательного анализа. Новая `ProposalReview` использует текущий `completion_manifest.contract_version` из `schemas/proposal-review-contract.json`. По умолчанию утилита только показывает план добавления; `--apply` разрешён после подтверждения пользователя. Записи добавляются только в управляемые реестры и проходят аудит до окончательного сохранения.

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
  "baseline_assessment_mode": "accepted_baseline",
  "baseline_snapshot_id": "BL-0001",
  "baseline_applicability_scope": "система освещения первого этажа",
  "baseline_requirement_ids": ["AR-0001"],
  "requirement_matches": [
    {"requirement_id": "AR-0001", "status": "partial", "quote_item_ids": ["QI-0001"], "notes": "..."}
  ],
  "unmatched_quote_item_ids": ["QI-0002"],
  "reference_comparisons": [],
  "baseline_limitations": [],
  "technical_checks": [
    {"check_id": "TC-001", "category": "interfaces", "criterion": "...", "status": "partial", "source_ids": ["QI-0001", "AR-0001"], "notes": "..."}
  ],
  "calculations": [
    {"calculation_id": "CALC-001", "formula": "quantity * unit_price", "inputs": [{"name": "quantity", "value": 2, "unit": "pcs", "source_ids": ["QI-0001"]}], "result": 2000, "unit": "RUB", "status": "verified"}
  ],
  "search_runs": [
    {"search_run_id": "SR-001", "status": "partial", "queries": ["..."], "checked_at": "2026-08-19", "region": "...", "source_urls": ["https://example.test/candidate"], "candidate_contractor_ids": ["CTR-0002"], "candidate_supplier_ids": [], "candidate_assessments": [{"counterparty_id": "CTR-0002", "counterparty_kind": "contractor", "comparability_status": "requires_quote", "basis": "тот же предмет и территория", "missing_information": ["нужно получить цену по единому заданию"], "source_urls": ["https://example.test/candidate"]}], "privacy_review": {"unnecessary_private_data_removed": true}}
  ],
  "finding_ids": ["FN-0001"],
  "alternative_ids": ["ALT-0001"],
  "essential_blockers": ["Нужен расчёт ..."],
  "contractor_questions": ["Предоставьте ..."],
  "mandatory_checks": [],
  "discipline_checks": [],
  "technical_alternative_assessments": [],
  "additional_model_checks": [],
  "additional_analysis_summary": "...",
  "foreman_assessment": {},
  "scope_boundary_matrix": [],
  "constructability_walkthrough": [],
  "cost_exposure": {},
  "contractor_assessment": [],
  "site_verification_plan": [],
  "acceptance_plan": [],
  "priority_risks": [],
  "risk_summary": "...",
  "completion_manifest": {}
}
```

Пустые массивы в примере обозначают разделы, которые нужно заполнить; с таким содержимым пакет не пройдёт проверку. Точный набор идентификаторов берётся из `schemas/proposal-review-contract.json`, а правила заполнения описаны в [обязательном контракте](mandatory-review-contract.md).

## Обязательные слои

- `baseline_assessment_mode` обязателен. Для `accepted_baseline` указываются текущий `baseline_snapshot_id`, точная `baseline_applicability_scope` и непустое подмножество применимых `baseline_requirement_ids` из снимка; каждый выбранный пункт покрывается ровно один раз.
- В `reference_only` поля снимка, `baseline_requirement_ids`, `requirement_matches` и связи `QuoteItem.approved_requirement_ids` пусты. `reference_comparisons` хранит точную версию полностью прочитанного документа, роль, область, формулировку, локатор, статус, строки КП и ограничения.
- В `no_relevant_documents` и базовая матрица, и `reference_comparisons` пусты. В обоих режимах без базы `baseline_limitations` явно перечисляет зависимые выводы, которые нельзя сделать. Эти ограничения не переносятся в `essential_blockers`, если не мешают независимой проверке.

- `mandatory_checks` содержит каждый универсальный `check_id` текущего контракта ровно один раз.
- `discipline_checks` содержит каждую комбинацию выбранного `discipline` и обязательного `axis_id` ровно один раз.
- `technical_alternative_assessments` содержит каждое обязательное направление технической альтернативы ровно один раз. Для `completed` нужны описание решения, применимость к проекту, преимущества, ограничения, влияние на исполнение, жизненный цикл, основания производительности, цены и реализуемости, рекомендация, источники и связанная `Alternative`.
- `additional_model_checks` — открытый массив новых проверок, не перечисленных в обязательном контракте. Он не ограничен известным перечнем идентификаторов.
- Каждый кандидат из `candidate_contractor_ids` и `candidate_supplier_ids` получает ровно одну запись `candidate_assessments` с видом стороны, статусом сопоставимости, основанием, недостающими данными и прямыми источниками.
- `foreman_assessment` фиксирует вердикт, готовность, решающие причины, условия и действия владельца.
- `scope_boundary_matrix` покрывает каждую строку КП ровно один раз и для каждого блока назначает все роли из `scope_responsibility_roles`.
- `constructability_walkthrough` покрывает каждую фазу `constructability_phases` ровно один раз.
- `cost_exposure` хранит валюту, формулу, пять числовых полей или `null`, источники и неизвестные расходы.
- `contractor_assessment` покрывает каждую ось `contractor_assessment_axes` ровно один раз.
- `site_verification_plan`, `acceptance_plan` и `priority_risks` содержат уникальные идентификаторы; каждый риск ссылается на зарегистрированный `Finding`.
- `completion_manifest` фиксирует версию контракта и точные списки всех фактически записанных проверок и прорабских разделов.

Для каждого обязательного элемента допустимы `completed`, `not_applicable`, `blocked` и `requires_specialist`. Выполненный пункт содержит результат, источники и наблюдения со `statement`, `locator`, `verification_status` и `source_ids`. Неприменимость доказывается, а блокировка перечисляет требуемые исходные данные. `ready_for_owner` допускает только `completed` и `not_applicable` во всех обязательных слоях.

`disciplines` — открытый список применённых профилей, а не классификатор одного направления. В одном анализе допустимы несколько значений.

`ready_for_owner` требует полной инвентаризации и чтения точной версии, классификации всех строк КП, полного покрытия выбранных применимых требований в `accepted_baseline` либо явного отделения справочного сравнения, отсутствия существенных блокеров, отдельного сопоставимого кандидата и совпадающего `completion_manifest`. `ready_for_contract` дополнительно требует `accepted_baseline`, условно положительного вердикта, проверяемого диапазона стоимости, закрытых проверок на объекте и завершённой оценки подрядчика. Ни одно из этих состояний не является решением владельца.

Полный перечень полей и допустимых идентификаторов берётся из машинного контракта; практический смысл разделов описан в [прорабской оценке](foreman-assessment.md).
