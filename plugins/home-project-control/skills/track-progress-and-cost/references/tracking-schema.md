# Схемы контроля

## work_items.csv

Допустимые `status`: `not_started`, `in_progress`, `claimed_complete`, `accepted`, `blocked`, `cancelled`.

`progress_percent` хранить от 0 до 100 только при наличии понятного основания. `accepted` требует ссылки `acceptance_document_id`, когда приёмка оформляется документом.

## costs.csv

Допустимые `status`: `proposed`, `invoiced`, `claimed_paid`, `confirmed_paid`, `refunded`, `cancelled`, `unknown`.

Для `confirmed_paid` обязательны `amount`, `currency`, `evidence_document_id` и точный `evidence_locator`.

## issues.csv

Допустимые `severity`: `critical`, `high`, `medium`, `low`, `unknown`.

Допустимые `status`: `open`, `in_review`, `resolved`, `accepted_risk`, `closed`, `unknown`.

Не присваивать тяжесть по интуиции. Указывать критерий: безопасность, невозможность продолжения, стоимость переделки, влияние на срок или документированное требование.

## changes.csv

Допустимые `status`: `proposed`, `under_review`, `approved`, `rejected`, `implemented_unapproved`, `superseded`.

В утверждённый бюджет включать только `approved` со ссылкой на решение владельца и документ.

## commitments.csv

Допустимые `status`: `open`, `due`, `claimed_complete`, `verified`, `waived`, `disputed`, `superseded`.

Просрочку показывать только при однозначном сроке или событии из указанного источника.

## acceptance.csv

Допустимые `status`: `not_presented`, `presented`, `conditionally_accepted`, `accepted`, `rejected`, `specialist_check_required`.

## procurement.csv

Допустимые `status`: `candidate`, `specified`, `approved_to_order`, `ordered`, `delivered`, `accepted`, `returned`, `cancelled`, `unknown`.

`approved_to_order` требует подтверждённых обязательных параметров, количества и места применения.

## Эксплуатационные связи

Элемент работ по обслуживанию, ремонту или замене должен указывать применимые `Site`, `Zone` и `Asset` доступным способом трассировки. Подтверждённое завершение связывается с приёмкой и `AssetEvent`; само значение `progress_percent` не обновляет фактическое состояние актива.

Плановую дату обслуживания выводить только из применимого регламента, договора или явно подтверждённого плана. Простой требует начала, окончания либо открытого статуса, затронутой функции и источника. Если денежное влияние простоя не рассчитано по согласованной методике, хранить его как неизвестное, а не ноль.

## Версионируемый управленческий цикл

- `cost_plans.jsonl` — ревизии сметы;
- `schedule_plans.jsonl` — ревизии календарного плана и рассчитанного сетевого графика;
- `management_baselines.jsonl` — принятые владельцем пары смета–график;
- `change_impact_assessments.jsonl` — влияние изменения на принятую стоимость и срок;
- `control_snapshots.jsonl` — датированные план-факт срезы и прогнозы.

`ControlSnapshot.confirmed_actual_cost_ids` перечисляет только подтверждённые платежи из `costs.csv`, относящиеся к `work_item_id` принятой сметы и датированные не позднее `data_date`. Их сумма должна совпадать с `confirmed_actual_cost`; платежы другого объёма или будущей даты в срез не попадают.

`ControlSnapshot.metrics` хранит принятую стоимость, утверждённые изменения, текущий бюджет, подтверждённые затраты, остаточную оценку, прогноз итога, денежное отклонение, плановый и фактический прогресс, плановую и освоенную стоимость, индексы, принятую и прогнозную даты завершения. Формулы хранятся рядом; `null` означает отсутствие расчётного основания, а не ноль.
