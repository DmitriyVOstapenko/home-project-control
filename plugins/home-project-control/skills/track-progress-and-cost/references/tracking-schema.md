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
