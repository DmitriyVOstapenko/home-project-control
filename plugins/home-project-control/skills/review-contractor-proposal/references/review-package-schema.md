# Пакет записи профессиональной проверки

Утилита `scripts/record_proposal_review.py` принимает транспортный JSON-пакет `schema_version: 1.0`. Эта версия относится к оболочке пакета и не равна версии обязательного анализа. Новая `ProposalReview` использует текущий `completion_manifest.contract_version` из `schemas/proposal-review-contract.json`. По умолчанию утилита только показывает план добавления; `--apply` разрешён после подтверждения пользователя. Записи добавляются только в управляемые реестры и проходят аудит до окончательного сохранения.

Допустимые разделы: `reading_runs`, `facts`, `contractors`, `suppliers`, `quotes`, `quote_items`, `equipment_options`, `price_observations`, `norm_references`, `findings`, `alternatives`, `project_packages`, `fact_extraction_runs`, `information_gaps`, `shared_resources`, `resource_demands`, `package_interfaces`, `coordination_issues`, `coordination_runs`, `proposal_reviews`.

Для `price_observations` обязательны `observed_at`, `region`, `currency`, `tax_context`, `delivery_context`, `availability_context` и прямой `source_url`. Для `norm_references` обязательны `designation`, точные `title`, `document_kind`, `version`, `document_status`, `jurisdiction`, `territory`, `checked_at`, `status_source_url`, `source_url` и `scope`. Атомарные требования и результаты соответствия предварительно записываются через `check-regulatory-compliance`.

Минимальная форма центральной записи:

```json
{
  "proposal_review_id": "PR-0001",
  "review_series_id": "PRS-0001",
  "review_revision": 1,
  "supersedes_proposal_review_id": "",
  "source_document_id": "DOC-0001",
  "document_version": 1,
  "sha256": "...",
  "quote_id": "Q-0001",
  "status": "in_progress",
  "disciplines": ["electrical", "equipment_supply"],
  "inventory_id": "DI-...",
  "reading_run_ids": ["RR-0001"],
  "fact_extraction_run_ids": ["FER-0001"],
  "project_package_ids": ["PKG-0001"],
  "information_gap_ids": ["GAP-0001"],
  "coordination_issue_ids": [],
  "compliance_assessment_ids": ["RCA-0001"],
  "context_mode": "as_is_and_baseline",
  "as_is_snapshot_id": "AIS-0001",
  "target_entity_ids": ["SYS-0001"],
  "context_limitations": [],
  "as_is_fact_matches": [
    {"fact_id": "F-0001", "status": "applied", "verification_status": "requires_confirmation", "decision_treatment": "risk_signal", "decision_impact": "...", "confirmation_action": "...", "if_confirmed": "...", "if_refuted": "...", "quote_item_ids": ["QI-0001"], "target_entity_ids": ["SYS-0001"], "notes": "...", "source_ids": ["F-0001", "QI-0001"]}
  ],
  "context_conflicts": [],
  "baseline_assessment_mode": "accepted_baseline",
  "baseline_snapshot_id": "BL-0001",
  "baseline_applicability_scope": "система освещения первого этажа",
  "baseline_requirement_ids": ["AR-0001"],
  "baseline_scope_classifications": [
    {"requirement_id": "AR-0001", "status": "applicable", "reason": "...", "source_ids": ["AR-0001"]}
  ],
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
  "decision_criteria": [],
  "alternative_comparisons": [],
  "price_comparisons": [],
  "clarification_requests": [],
  "coordination_run_ids": [],
  "management_scenarios": [],
  "challenge_review": {},
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

- `ProposalReview` неизменяем: первая запись имеет ревизию `1`, следующая запись той же серии ссылается на непосредственно предшествующую.
- `context_mode` обязателен. Для режима с фактическим состоянием указываются текущий `as_is_snapshot_id`, затрагиваемые сущности и классификация каждого исходного факта снимка. В каждой записи `as_is_fact_matches` точный статус достоверности переносится из `Fact`, способ учёта выбирается из `constraint`, `risk_signal`, `scenario_input`, `monitor`, `not_applicable`, а влияние на вывод описывается явно. Для конфликтующего или требующего подтверждения факта обязательны проверка и последствия подтверждения и опровержения. Для режима с принятой базой указываются текущий `baseline_snapshot_id`, точная область, классификация каждого требования снимка и непустое подмножество применимых `baseline_requirement_ids`.
- `context_conflicts` отдельно хранит противоречия факта, проекта и предложения; открытый блокирующий конфликт не допускает договорную готовность.
- В `reference_only` поля снимка, `baseline_requirement_ids`, `requirement_matches` и связи `QuoteItem.approved_requirement_ids` пусты. `reference_comparisons` хранит точную версию полностью прочитанного документа, роль, область, формулировку, локатор, статус, строки КП и ограничения.
- В `no_relevant_documents` и базовая матрица, и `reference_comparisons` пусты. В обоих режимах без базы `baseline_limitations` явно перечисляет зависимые выводы, которые нельзя сделать. Эти ограничения не переносятся в `essential_blockers`, если не мешают независимой проверке.

- `mandatory_checks` содержит каждый универсальный `check_id` текущего контракта ровно один раз.
- `discipline_checks` содержит каждую комбинацию выбранного `discipline` и обязательного `axis_id` ровно один раз.
- `technical_alternative_assessments` содержит каждое обязательное направление технической альтернативы ровно один раз. Для `completed` нужны описание решения, применимость к проекту, преимущества, ограничения, влияние на исполнение, жизненный цикл, основания производительности, цены и реализуемости, рекомендация, источники и связанная `Alternative`.
- `additional_model_checks` — открытый массив новых проверок, не перечисленных в обязательном контракте. Он не ограничен известным перечнем идентификаторов.
- `decision_criteria` отделяет критерии владельца, проекта и модели. `alternative_comparisons` содержит ровно одну полную матрицу для каждого зарегистрированного варианта.
- `price_comparisons` хранит нормализованное основание цены и связанные `PriceObservation`; несопоставимые наблюдения остаются ограничениями.
- Каждый `InformationGap` получает ровно один `clarification_request`. Строковый `contractor_questions` является только производным кратким представлением вопросов, адресованных подрядчику или поставщику.
- `management_scenarios` содержит ровно один сценарий для каждого варианта. Статус `complete` требует существующие готовые `CostPlan` и `SchedulePlan` той же проектной базы. Для нескольких пакетов нужен завершённый совместный `CoordinationRun`.
- `challenge_review` фиксирует контраргумент, сценарии отказа и данные, способные изменить рекомендацию.
- Каждый кандидат из `candidate_contractor_ids` и `candidate_supplier_ids` получает ровно одну запись `candidate_assessments` с видом стороны, статусом сопоставимости, основанием, недостающими данными и прямыми источниками.
- `foreman_assessment` фиксирует вердикт, готовность, точный `decision_request`, решающие причины, условия и действия владельца. `preferred_alternative_id` ссылается на зарегистрированную альтернативу либо остаётся пустым; `preferred_alternative_rationale` в обоих случаях объясняет основание предпочтения или его отсутствие.
- `fact_extraction_run_ids` содержит завершённый запуск смыслового извлечения точной версии КП, а `project_package_ids` — хотя бы один применимый пакет. Пробелы и известные межпакетные коллизии связываются отдельными массивами.
- `compliance_assessment_ids` содержит завершённые нормативные оценки, которыми закрывается `norms_and_specialist_boundary`. При доказанной неприменимости массив может быть пустым, а обязательный пункт получает `not_applicable` с источниками и объяснением.
- `scope_boundary_matrix` покрывает каждую строку КП ровно один раз и для каждого блока назначает все роли из `scope_responsibility_roles`.
- `constructability_walkthrough` покрывает каждую фазу `constructability_phases` ровно один раз.
- `cost_exposure` хранит валюту, формулу, пять числовых полей или `null`, источники и неизвестные расходы.
- `contractor_assessment` покрывает каждую ось `contractor_assessment_axes` ровно один раз.
- `site_verification_plan`, `acceptance_plan` и `priority_risks` содержат уникальные идентификаторы; каждый риск ссылается на зарегистрированный `Finding`.
- `completion_manifest` фиксирует версию контракта и точные списки всех фактически записанных проверок и прорабских разделов.

Для каждого обязательного элемента допустимы `completed`, `not_applicable`, `blocked` и `requires_specialist`. Выполненный пункт содержит результат, источники и наблюдения со `statement`, `locator`, `verification_status` и `source_ids`. Неприменимость доказывается, а блокировка перечисляет требуемые исходные данные. `ready_for_owner` допускает только `completed` и `not_applicable` во всех обязательных слоях.

`disciplines` — открытый список применённых профилей, а не классификатор одного направления. В одном анализе допустимы несколько значений.

`ready_for_owner` требует полной инвентаризации, чтения и смыслового извлечения точной версии, классификации всех строк КП и выбранного контекста, полной матрицы вариантов, сопоставимых цен, структурированных уточнений, управленческих сценариев, успешного оппонирующего прохода, отдельного сопоставимого кандидата и совпадающего `completion_manifest`. `ready_for_contract` дополнительно требует принятую `BaselineSnapshot` и актуальную `AsIsSnapshot` там, где решение зависит от существующего состояния, условно положительный вердикт, закрытые блокирующие конфликты, уточнения и проверки на объекте, завершённую оценку подрядчика и готовые `CostPlan` и `SchedulePlan` предпочтительного варианта. Ни одно из этих состояний не является решением владельца.

Полный перечень полей и допустимых идентификаторов берётся из машинного контракта; практический смысл разделов описан в [прорабской оценке](foreman-assessment.md).
