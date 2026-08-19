from __future__ import annotations

import csv
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPT_ROOT = REPO_ROOT / "plugins" / "home-project-control" / "skills" / "manage-project-evidence" / "scripts"
INIT = SCRIPT_ROOT / "init_project.py"
INSPECT = SCRIPT_ROOT / "inspect_project.py"
REPAIR = SCRIPT_ROOT / "repair_project.py"
INDEX = SCRIPT_ROOT / "index_documents.py"
INGEST = SCRIPT_ROOT / "ingest_documents.py"
AUDIT = SCRIPT_ROOT / "audit_project.py"
INVENTORY = SCRIPT_ROOT / "inventory_document.py"
RECORD_BASELINE = SCRIPT_ROOT / "record_baseline_snapshot.py"
RECORD_ANALYSIS = SCRIPT_ROOT / "record_analysis_cycle.py"
BUILD_CONTEXT = SCRIPT_ROOT / "build_project_context.py"
PROPOSAL_SCRIPT_ROOT = REPO_ROOT / "plugins" / "home-project-control" / "skills" / "review-contractor-proposal" / "scripts"
RECORD_PROPOSAL = PROPOSAL_SCRIPT_ROOT / "record_proposal_review.py"
BUILD_DOSSIER = PROPOSAL_SCRIPT_ROOT / "build_proposal_dossier.py"
REGULATORY_SCRIPT_ROOT = REPO_ROOT / "plugins" / "home-project-control" / "skills" / "check-regulatory-compliance" / "scripts"
RECORD_REGULATORY = REGULATORY_SCRIPT_ROOT / "record_regulatory_assessment.py"
CHECK_REGULATORY_UPDATES = REGULATORY_SCRIPT_ROOT / "check_regulatory_updates.py"
DASHBOARD = REPO_ROOT / "plugins" / "home-project-control" / "skills" / "track-progress-and-cost" / "scripts" / "build_dashboard.py"
MANAGEMENT = REPO_ROOT / "plugins" / "home-project-control" / "skills" / "track-progress-and-cost" / "scripts" / "record_management_cycle.py"
STRUCTURE = json.loads(
    (REPO_ROOT / "plugins" / "home-project-control" / "schemas" / "project-structure.json").read_text(
        encoding="utf-8"
    )
)
PROPOSAL_CONTRACT = json.loads(
    (REPO_ROOT / "plugins" / "home-project-control" / "schemas" / "proposal-review-contract.json").read_text(
        encoding="utf-8"
    )
)


def run_script(script: Path, *args: object) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-X", "utf8", str(script), *(str(arg) for arg in args)],
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )


def version_tuple(value: str) -> tuple[int, ...]:
    return tuple(int(part) for part in value.split("."))


def create_legacy_project(project: Path, version: str) -> None:
    project.mkdir(parents=True)
    for relative in STRUCTURE["folders"][:14]:
        (project / relative).mkdir()
    control = project / ".home-control"
    (control / "reports").mkdir(parents=True)
    if version_tuple(version) >= (2, 0):
        (control / "summaries").mkdir()

    marker = {
        "schema_version": "2.2",
        "project_id": f"legacy-{version}",
        "name": f"Legacy {version}",
        "created_by": {"plugin_id": STRUCTURE["plugin_id"], "structure_version": version},
        "project_root": str(project.resolve()),
        "folder_binding": {"absolute_path": str(project.resolve())},
        "location": "legacy location retained verbatim",
        "systems": {"legacy-system": {"status": "recorded"}},
    }
    (control / "project.json").write_text(
        json.dumps(marker, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    document_schema = "1.0" if version == "1.0" else "2.0"
    documents = {
        "schema_version": document_schema,
        "indexed_at_utc": "2026-01-01T00:00:00+00:00",
        "items": [
            {
                "document_id": "legacy-document",
                "relative_path": "legacy.pdf",
                "status": "missing",
                "sha256": "legacy-sha",
                "versions": [{"version": 1, "sha256": "legacy-sha"}],
            }
        ],
    }
    (control / "documents.json").write_text(
        json.dumps(documents, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )

    for relative, metadata in STRUCTURE["jsonl_files"].items():
        if version_tuple(metadata["introduced_in"]) > version_tuple(version):
            continue
        path = project / relative
        if path.name == "facts.jsonl":
            content = json.dumps({"fact_id": "F-LEGACY", "statement": "legacy"}, ensure_ascii=False) + "\n"
        elif path.name == "equipment_options.jsonl":
            content = json.dumps(
                {"equipment_option_id": "EO-LEGACY", "description": "candidate, not installed"},
                ensure_ascii=False,
            ) + "\n"
        elif path.name == "norm_references.jsonl":
            content = json.dumps(
                {
                    "norm_reference_id": "NR-LEGACY",
                    "title": "Legacy normative reference",
                    "version": "legacy edition",
                    "territory": "legacy territory",
                    "checked_at": "2026-01-01",
                    "locator": "legacy clause",
                    "source_url": "https://example.test/legacy-norm",
                    "scope": "legacy scope",
                },
                ensure_ascii=False,
            ) + "\n"
        else:
            content = ""
        path.write_text(content, encoding="utf-8")

    for relative, headers in STRUCTURE["csv_files"].items():
        path = project / relative
        with path.open("w", encoding="utf-8-sig", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=headers)
            writer.writeheader()
            if path.name == "costs.csv":
                writer.writerow(
                    {
                        "cost_id": "C-LEGACY",
                        "date": "2026-01-01",
                        "description": "preserve me",
                        "amount": "100",
                        "currency": "RUB",
                        "status": "proposed",
                    }
                )


def file_snapshot(project: Path) -> dict[str, bytes]:
    return {
        path.relative_to(project).as_posix(): path.read_bytes()
        for path in project.rglob("*")
        if path.is_file()
    }


def write_jsonl(path: Path, *records: dict) -> None:
    path.write_text(
        "".join(json.dumps(record, ensure_ascii=False) + "\n" for record in records),
        encoding="utf-8",
    )


def read_jsonl(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def append_csv_row(path: Path, row: dict[str, object]) -> None:
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        headers = list(csv.DictReader(handle).fieldnames or [])
    with path.open("a", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=headers, extrasaction="ignore")
        writer.writerow(row)


def management_package() -> dict:
    return {
        "schema_version": "1.0",
        "cost_plans": [{
            "cost_plan_id": "CPL-1",
            "plan_series_id": "COST-MAIN",
            "revision": 1,
            "status": "ready_for_baseline",
            "baseline_snapshot_id": "BL-MGT",
            "valuation_date": "2026-01-01",
            "currency": "RUB",
            "items": [
                {
                    "item_id": "CPI-1",
                    "work_item_id": "W-1",
                    "description": "Подготовительные работы",
                    "quantity": 1,
                    "unit": "компл.",
                    "unit_rate": 100,
                    "amount": 100,
                    "cost_basis": "calculation",
                    "source_ids": ["F-MGT"],
                    "quote_item_ids": [],
                    "price_observation_ids": [],
                },
                {
                    "item_id": "CPI-2",
                    "work_item_id": "W-2",
                    "description": "Основные работы",
                    "quantity": 2,
                    "unit": "компл.",
                    "unit_rate": 100,
                    "amount": 200,
                    "cost_basis": "calculation",
                    "source_ids": ["F-MGT"],
                    "quote_item_ids": [],
                    "price_observation_ids": [],
                },
            ],
        }],
        "schedule_plans": [{
            "schedule_plan_id": "SPL-1",
            "plan_series_id": "SCHEDULE-MAIN",
            "revision": 1,
            "status": "ready_for_baseline",
            "baseline_snapshot_id": "BL-MGT",
            "project_start": "2026-01-05",
            "calendar": {"working_weekdays": [0, 1, 2, 3, 4], "holidays": []},
            "activities": [
                {
                    "activity_id": "ACT-1",
                    "work_item_id": "W-1",
                    "title": "Подготовка",
                    "duration_workdays": 5,
                    "date_basis": "calculated",
                    "predecessors": [],
                    "source_ids": ["F-MGT"],
                },
                {
                    "activity_id": "ACT-2",
                    "work_item_id": "W-2",
                    "title": "Основные работы",
                    "duration_workdays": 3,
                    "date_basis": "calculated",
                    "predecessors": [{"activity_id": "ACT-1", "relationship": "FS", "lag_workdays": 0}],
                    "source_ids": ["F-MGT"],
                },
            ],
        }],
        "management_baselines": [{
            "management_baseline_id": "MBL-1",
            "baseline_version": 1,
            "status": "accepted",
            "cost_plan_id": "CPL-1",
            "schedule_plan_id": "SPL-1",
            "owner_decision_id": "D-MGT",
            "accepted_at": "2026-01-02T12:00:00+03:00",
            "supersedes_management_baseline_id": "",
        }],
        "change_impact_assessments": [{
            "change_impact_assessment_id": "CIA-1",
            "management_baseline_id": "MBL-1",
            "change_id": "CH-1",
            "status": "approved",
            "cost_delta": 50,
            "schedule_delta_workdays": 1,
            "currency": "RUB",
            "affected_cost_item_ids": ["CPI-2"],
            "affected_activity_ids": ["ACT-2"],
            "decision_id": "D-MGT",
            "effective_date": "2026-01-02",
            "source_ids": ["F-MGT"],
        }],
        "control_snapshots": [{
            "control_snapshot_id": "CSN-1",
            "management_baseline_id": "MBL-1",
            "data_date": "2026-01-07",
            "status": "complete",
            "currency": "RUB",
            "estimate_to_complete": 250,
            "forecast_finish": "2026-01-16",
            "change_impact_assessment_ids": ["CIA-1"],
            "progress_measurements": [
                {"activity_id": "ACT-1", "weight": 0.4, "physical_progress_percent": 50, "source_ids": ["F-MGT"]},
                {"activity_id": "ACT-2", "weight": 0.6, "physical_progress_percent": 0, "source_ids": ["F-MGT"]},
            ],
            "source_ids": ["F-MGT"],
        }],
    }


def complete_proposal_contract(
    disciplines: list[str],
    source_ids: list[str],
    alternative_ids: list[str],
    quote_item_ids: list[str],
    requirement_ids: list[str],
    finding_ids: list[str],
    compliance_assessment_ids: list[str],
) -> dict:
    def observations(statement: str) -> list[dict]:
        return [{
            "statement": statement,
            "locator": "тестовый источник, строка 1",
            "verification_status": "verified",
            "source_ids": source_ids,
        }]

    mandatory = [
        {
            "check_id": definition["check_id"],
            "status": "completed",
            "result": f"Проверено: {definition['title']}",
            "source_ids": source_ids,
            "observations": observations(f"Подтверждено по пункту {definition['check_id']}"),
        }
        for definition in PROPOSAL_CONTRACT["universal_checks"]
    ]
    for item in mandatory:
        if item["check_id"] == "norms_and_specialist_boundary":
            item["source_ids"] = [*source_ids, *compliance_assessment_ids]
            item["observations"][0]["source_ids"] = [*source_ids, *compliance_assessment_ids]
    discipline_checks = [
        {
            "discipline": discipline,
            "axis_id": axis["axis_id"],
            "status": "completed",
            "result": f"{discipline}: {axis['title']}",
            "source_ids": source_ids,
            "observations": observations(f"Проверена ось {axis['axis_id']} для {discipline}"),
            "criteria_checked": ["состав", "стыки", "результат"],
            "field_risks": [],
            "required_site_checks": ["контрольный осмотр тестовой зоны"],
        }
        for discipline in disciplines
        for axis in PROPOSAL_CONTRACT["discipline_axes"]
    ]
    technical_alternatives = [
        {
            "track_id": track["track_id"],
            "status": "completed",
            "result": f"Исследован вариант: {track['title']}",
            "source_ids": source_ids,
            "observations": observations(f"Сопоставлен вариант {track['track_id']}"),
            "alternative_ids": [alternative_id],
            "solution": track["title"],
            "project_fit": "Сопоставлен с требованиями тестового проекта",
            "benefits": "Зафиксированы преимущества",
            "drawbacks": "Зафиксированы ограничения",
            "implementation_impacts": "Проверено влияние на монтаж и смежные системы",
            "lifecycle_cost_notes": "Состав стоимости жизненного цикла обозначен",
            "performance_basis": "Функция и требуемый результат сопоставлены с AR-1",
            "cost_basis": "Стоимость сравнивается по одинаковому объёму",
            "constructability_basis": "Доступ и последовательность монтажа проверены по тестовым данным",
            "recommendation": "Запросить сопоставимое уточнение до выбора",
        }
        for track, alternative_id in zip(PROPOSAL_CONTRACT["technical_alternative_tracks"], alternative_ids)
    ]
    additional = [{
        "check_id": "MODEL-1",
        "question": "Есть ли дополнительный риск, не покрытый обязательным контрактом?",
        "status": "completed",
        "result": "Дополнительных рисков в тестовом примере не выявлено",
        "source_ids": source_ids,
        "observations": observations("Выполнен открытый поиск дополнительных рисков"),
    }]
    scope_rows = [{
        "scope_id": "SCOPE-1",
        "result": "Поставка и монтаж светильников выделены в единый тестовый блок",
        "quote_item_ids": quote_item_ids,
        "requirement_ids": requirement_ids,
        "responsibilities": {role: "CTR-1" for role in PROPOSAL_CONTRACT["scope_responsibility_roles"]},
        "gaps": [],
        "source_ids": source_ids,
    }]
    phases = [{
        "phase_id": phase["phase_id"],
        "status": "completed",
        "result": f"Проверена фаза: {phase['title']}",
        "source_ids": source_ids,
        "observations": observations(f"Документально проверена фаза {phase['phase_id']}"),
        "risks": [],
        "actions": ["сохранить критерий в договорном приложении"],
    } for phase in PROPOSAL_CONTRACT["constructability_phases"]]
    contractor_assessment = [{
        "axis_id": axis["axis_id"],
        "status": "completed",
        "result": f"Проверена ось исполнителя: {axis['title']}",
        "source_ids": source_ids,
        "observations": observations(f"Подтверждена ось {axis['axis_id']}"),
    } for axis in PROPOSAL_CONTRACT["contractor_assessment_axes"]]
    site_plan = [{
        "verification_id": "SITE-1",
        "status": "completed",
        "subject": "готовность тестовой зоны к монтажу",
        "method": "визуальный осмотр и контрольный обмер",
        "responsible_role": "технический заказчик",
        "required_before": "подписание договора",
        "consequence_if_unverified": "не подтверждён объём подготовительных работ",
        "source_ids": source_ids,
    }]
    acceptance_plan = [{
        "acceptance_id": "ACC-1",
        "result": "два светильника установлены и работоспособны",
        "criterion": "2 шт., без повреждений, включаются штатным управлением",
        "method": "пересчёт, осмотр и функциональная проверка",
        "timing": "до подписания акта",
        "responsible_party": "владелец или технический заказчик",
        "evidence_required": ["акт", "фотографии", "результат функциональной проверки"],
        "source_ids": source_ids,
    }]
    priority_risks = [{
        "risk_id": "RISK-1",
        "finding_id": finding_ids[0],
        "urgency": "before_contract",
        "impact_lanes": ["cost", "quality"],
        "consequence": "без уточнения гарантии часть результата нельзя сопоставить",
        "mitigation": "закрепить срок и объём гарантии в договоре",
        "owner_action": "получить письменное подтверждение подрядчика",
        "source_ids": source_ids,
    }]
    return {
        "mandatory_checks": mandatory,
        "discipline_checks": discipline_checks,
        "technical_alternative_assessments": technical_alternatives,
        "additional_model_checks": additional,
        "additional_analysis_summary": "Открытая проверка дополнительных рисков выполнена; новых классов риска не найдено.",
        "foreman_assessment": {
            "verdict": "conditionally_recommended",
            "decision_readiness": "ready_for_contract",
            "summary": "Тестовое предложение можно рассматривать при закреплении гарантии и критериев приёмки.",
            "decision_request": "Решить, запрашивать ли уточнённую редакцию КП на указанных условиях",
            "preferred_alternative_id": "ALT-OPT",
            "preferred_alternative_rationale": "Оптимизация исходного решения требует наименьшего изменения подтверждённого объёма.",
            "decisive_reasons": ["объём и арифметика сопоставлены", "приёмка измерима"],
            "conditions_before_contract": ["закрепить гарантию"],
            "conditions_before_work": ["подтвердить готовность зоны"],
            "owner_next_actions": ["получить уточнённую редакцию условий"],
            "source_ids": source_ids,
        },
        "scope_boundary_matrix": scope_rows,
        "constructability_walkthrough": phases,
        "cost_exposure": {
            "currency": "RUB",
            "formula": "quoted_total + known_excluded_amount",
            "status": "verified",
            "quoted_total": 2000,
            "confirmed_included_amount": 2000,
            "known_excluded_amount": 0,
            "estimated_total_low": 2000,
            "estimated_total_high": 2000,
            "unknown_exposures": [],
            "source_ids": source_ids,
        },
        "contractor_assessment": contractor_assessment,
        "site_verification_plan": site_plan,
        "acceptance_plan": acceptance_plan,
        "priority_risks": priority_risks,
        "risk_summary": "Один управляемый риск до договора: требуется закрепить гарантию.",
        "completion_manifest": {
            "contract_version": PROPOSAL_CONTRACT["contract_version"],
            "mandatory_check_ids": [value["check_id"] for value in mandatory],
            "discipline_check_keys": [f"{value['discipline']}|{value['axis_id']}" for value in discipline_checks],
            "technical_alternative_track_ids": [value["track_id"] for value in technical_alternatives],
            "additional_model_check_ids": [value["check_id"] for value in additional],
            "scope_ids": [value["scope_id"] for value in scope_rows],
            "constructability_phase_ids": [value["phase_id"] for value in phases],
            "contractor_assessment_axis_ids": [value["axis_id"] for value in contractor_assessment],
            "site_verification_ids": [value["verification_id"] for value in site_plan],
            "acceptance_plan_ids": [value["acceptance_id"] for value in acceptance_plan],
            "priority_risk_ids": [value["risk_id"] for value in priority_risks],
        },
    }


def regulatory_test_package(target_id: str, fact_id: str) -> dict:
    return {
        "schema_version": "1.0",
        "norm_references": [{
            "norm_reference_id": "NR-1",
            "designation": "СП TEST-2026",
            "title": "Тестовые требования к монтажу освещения",
            "version": "2026",
            "document_kind": "code_of_rules",
            "document_status": "active",
            "jurisdiction": "RU",
            "territory": "Российская Федерация",
            "checked_at": "2026-08-19",
            "status_source_url": "https://example.test/norm-status",
            "source_url": "https://example.test/norm-text",
            "scope": "Монтаж освещения в тестовой зоне",
            "supersedes_norm_reference_ids": [],
            "replacement_norm_reference_ids": [],
        }],
        "regulatory_requirements": [{
            "regulatory_requirement_id": "RQR-1",
            "norm_reference_id": "NR-1",
            "locator": "пункт 1.1",
            "statement": "Результат монтажа должен быть проверяемым",
            "scope_conditions": "При монтаже освещения в тестовой зоне",
            "verification_method": "Функциональная проверка",
            "specialist_boundary": "Электробезопасность подтверждает профильный специалист",
            "source_url": "https://example.test/norm-text#p1",
            "extracted_at": "2026-08-19",
            "verification_status": "verified",
        }],
        "compliance_assessments": [{
            "compliance_assessment_id": "RCA-1",
            "assessment_type": "contractor_proposal",
            "jurisdiction": "RU",
            "scope": "Монтаж освещения в тестовой зоне",
            "assessed_at": "2026-08-19",
            "status": "complete",
            "target_entity_ids": [target_id],
            "norm_reference_ids": ["NR-1"],
            "expected_requirement_ids": ["RQR-1"],
            "checked_requirement_ids": ["RQR-1"],
            "result_ids": ["RCR-1"],
            "information_gap_ids": [],
            "limitations": ["Проектные расчёты и измерения профильного специалиста не представлены"],
        }],
        "compliance_results": [{
            "compliance_result_id": "RCR-1",
            "assessment_id": "RCA-1",
            "requirement_id": "RQR-1",
            "target_entity_ids": [target_id],
            "applicability_status": "applicable_by_project",
            "compliance_status": "conforms",
            "basis": "Проверяемый результат следует из зарегистрированного факта",
            "checked_at": "2026-08-19",
            "evidence_fact_ids": [fact_id],
            "finding_ids": [],
            "information_gap_ids": [],
        }],
        "regulatory_sync_runs": [],
    }


class ProjectToolsTest(unittest.TestCase):
    def test_new_project_passes_inspection(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            project = Path(temporary) / "project"
            dry_run = run_script(INIT, project, "--name", "Test", "--dry-run")
            self.assertEqual(dry_run.returncode, 0, dry_run.stderr)
            self.assertFalse(project.exists())

            created = run_script(INIT, project, "--name", "Test")
            self.assertEqual(created.returncode, 0, created.stderr)
            inspected = run_script(INSPECT, project)
            self.assertEqual(inspected.returncode, 0, inspected.stderr)
            result = json.loads(inspected.stdout)
            self.assertEqual(result["status"], "existing_project_ready")
            self.assertTrue(result["gate_passed"])
            self.assertTrue((project / ".home-control" / "summaries").is_dir())
            self.assertTrue((project / ".home-control" / "reading_runs.jsonl").is_file())
            self.assertTrue((project / ".home-control" / "approved_requirements.jsonl").is_file())
            self.assertTrue((project / ".home-control" / "baseline_snapshots.jsonl").is_file())
            self.assertTrue((project / ".home-control" / "quote_items.jsonl").is_file())
            self.assertTrue((project / ".home-control" / "sites.jsonl").is_file())
            self.assertTrue((project / ".home-control" / "assets.jsonl").is_file())
            self.assertTrue((project / ".home-control" / "asset_events.jsonl").is_file())
            self.assertTrue((project / ".home-control" / "document_inventories.jsonl").is_file())
            self.assertTrue((project / ".home-control" / "proposal_reviews.jsonl").is_file())
            self.assertTrue((project / ".home-control" / "project_packages.jsonl").is_file())
            self.assertTrue((project / ".home-control" / "fact_extraction_runs.jsonl").is_file())
            self.assertTrue((project / ".home-control" / "information_gaps.jsonl").is_file())
            self.assertTrue((project / ".home-control" / "coordination_runs.jsonl").is_file())
            self.assertTrue((project / ".home-control" / "regulatory_requirements.jsonl").is_file())
            self.assertTrue((project / ".home-control" / "compliance_assessments.jsonl").is_file())
            self.assertTrue((project / ".home-control" / "compliance_results.jsonl").is_file())
            self.assertTrue((project / ".home-control" / "regulatory_sync_runs.jsonl").is_file())
            self.assertTrue((project / ".home-control" / "document_intake_batches.jsonl").is_file())
            self.assertTrue((project / ".home-control" / "as_is_snapshots.jsonl").is_file())
            self.assertTrue((project / ".home-control" / "analysis_requests.jsonl").is_file())
            self.assertTrue((project / ".home-control" / "reports" / "proposals").is_dir())

    def test_audit_reports_duplicate_document_ids(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            project = Path(temporary) / "project"
            created = run_script(INIT, project, "--name", "Duplicate documents")
            self.assertEqual(created.returncode, 0, created.stderr)

            duplicate = {
                "document_id": "duplicate-document",
                "relative_path": "document.pdf",
                "status": "active",
                "versions": [{"version": 1, "sha256": "sha-1"}],
            }
            documents_path = project / ".home-control" / "documents.json"
            documents = json.loads(documents_path.read_text(encoding="utf-8"))
            documents["items"] = [duplicate, dict(duplicate)]
            documents_path.write_text(
                json.dumps(documents, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )

            audited = run_script(AUDIT, project)
            self.assertEqual(audited.returncode, 1, audited.stderr)
            self.assertIn("duplicate document_id duplicate-document", audited.stdout)

    def test_analysis_cycle_package_previews_validates_and_appends_atomically(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            project = Path(temporary) / "project"
            self.assertEqual(run_script(INIT, project).returncode, 0)
            package = {
                "schema_version": "1.0",
                "facts": [{
                    "fact_id": "F-PKG-1",
                    "statement": "Владелец определил пакет проверки электроснабжения",
                    "statement_kind": "source_fact",
                    "evidence_origin": "owner_confirmation",
                    "verification_status": "verified",
                    "locator": "тестовая задача, сообщение владельца",
                    "recorded_at": "2026-08-19",
                    "discipline_ids": ["electrical"],
                    "package_ids": ["PKG-POWER"],
                    "site_ids": [],
                    "zone_ids": [],
                    "system_ids": [],
                }],
                "project_packages": [{
                    "package_id": "PKG-POWER",
                    "name": "Проверка электроснабжения",
                    "goal": "Определить достаточность исходных данных",
                    "status": "in_analysis",
                    "disciplines": ["electrical"],
                    "source_document_versions": [],
                    "fact_ids": ["F-PKG-1"],
                    "requirement_ids": [],
                    "information_gap_ids": [],
                    "site_ids": [],
                    "zone_ids": [],
                    "system_ids": [],
                }],
            }
            path = project / "analysis-package.json"
            path.write_text(json.dumps(package, ensure_ascii=False, indent=2), encoding="utf-8")
            facts_path = project / ".home-control" / "facts.jsonl"
            before = facts_path.read_bytes()

            preview = run_script(RECORD_ANALYSIS, project, path)
            self.assertEqual(preview.returncode, 0, preview.stderr)
            self.assertEqual(facts_path.read_bytes(), before)
            self.assertEqual(json.loads(preview.stdout)["append"]["facts"], 1)

            applied = run_script(RECORD_ANALYSIS, project, path, "--apply")
            self.assertEqual(applied.returncode, 0, applied.stderr)
            self.assertIn("F-PKG-1", facts_path.read_text(encoding="utf-8"))
            self.assertEqual(run_script(AUDIT, project).returncode, 0)

            invalid = json.loads(json.dumps(package))
            invalid["facts"][0]["fact_id"] = "F-PKG-BAD"
            invalid["facts"][0]["package_ids"] = ["PKG-POWER"]
            invalid["facts"][0].pop("locator")
            invalid_path = project / "invalid-analysis-package.json"
            invalid_path.write_text(json.dumps(invalid, ensure_ascii=False), encoding="utf-8")
            rejected = run_script(RECORD_ANALYSIS, project, invalid_path)
            self.assertEqual(rejected.returncode, 2)
            self.assertIn("missing required field locator", rejected.stderr)

    def test_as_is_snapshot_analysis_request_and_context_card_are_persistent(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            project = Path(temporary) / "project"
            self.assertEqual(run_script(INIT, project, "--name", "Тестовый дом").returncode, 0)
            package = {
                "schema_version": "1.0",
                "facts": [{
                    "fact_id": "F-ASIS-1",
                    "statement": "Владелец сообщил, что дом находится на этапе эксплуатации",
                    "statement_kind": "source_fact",
                    "evidence_origin": "owner_confirmation",
                    "verification_status": "verified",
                    "locator": "тестовая задача, сообщение владельца",
                    "recorded_at": "2026-08-19",
                    "discipline_ids": [],
                    "package_ids": [],
                    "site_ids": [],
                    "zone_ids": [],
                    "system_ids": [],
                }],
                "decisions": [{
                    "decision_id": "D-ASIS-1",
                    "decision_type": "as_is_snapshot_acceptance",
                    "decision": "Использовать показанный снимок как рабочее состояние объекта",
                    "status": "approved",
                    "approved_by": "owner",
                    "approved_at": "2026-08-19",
                    "source_fact_ids": ["F-ASIS-1"],
                    "notes": "Решение не заменяет техническую верификацию факта",
                }],
                "as_is_snapshots": [{
                    "as_is_snapshot_id": "AIS-1",
                    "snapshot_version": 1,
                    "scope": "Дом в целом",
                    "captured_at": "2026-08-19",
                    "owner_decision_id": "D-ASIS-1",
                    "supersedes_as_is_snapshot_id": "",
                    "document_versions": [],
                    "source_fact_ids": ["F-ASIS-1"],
                    "information_gap_ids": [],
                    "site_ids": [],
                    "zone_ids": [],
                    "physical_element_ids": [],
                    "system_ids": [],
                    "asset_ids": [],
                    "route_ids": [],
                    "asset_event_ids": [],
                    "condition_assessment_ids": [],
                    "limitations": ["Техническое обследование в тесте не выполнялось"],
                }],
                "analysis_requests": [{
                    "analysis_request_id": "ANR-1",
                    "request_series_id": "SERIES-1",
                    "request_version": 1,
                    "request_text": "Предложить следующий этап проверки дома",
                    "request_type": "question",
                    "status": "open",
                    "requested_at": "2026-08-19",
                    "context_mode": "as_is_only",
                    "as_is_snapshot_id": "AIS-1",
                    "baseline_snapshot_id": "",
                    "source_document_ids": [],
                    "package_ids": [],
                    "information_gap_ids": [],
                    "target_entity_ids": [],
                    "requested_outputs": ["Краткая рекомендация и следующий шаг"],
                    "result_paths": [],
                    "supersedes_analysis_request_id": "",
                }],
            }
            package_path = project / "context-package.json"
            package_path.write_text(json.dumps(package, ensure_ascii=False, indent=2), encoding="utf-8")
            preview = run_script(RECORD_ANALYSIS, project, package_path)
            self.assertEqual(preview.returncode, 0, preview.stderr)
            applied = run_script(RECORD_ANALYSIS, project, package_path, "--apply")
            self.assertEqual(applied.returncode, 0, applied.stderr)
            self.assertEqual(run_script(AUDIT, project).returncode, 0)

            resumed_request = dict(package["analysis_requests"][0])
            resumed_request.update({
                "analysis_request_id": "ANR-2",
                "request_version": 2,
                "status": "ready_to_resume",
                "revised_at": "2026-08-20",
                "supersedes_analysis_request_id": "ANR-1",
            })
            revision_path = project / "request-revision.json"
            revision_path.write_text(
                json.dumps(
                    {"schema_version": "1.0", "analysis_requests": [resumed_request]},
                    ensure_ascii=False,
                    indent=2,
                ),
                encoding="utf-8",
            )
            self.assertEqual(run_script(RECORD_ANALYSIS, project, revision_path, "--apply").returncode, 0)
            self.assertEqual(run_script(AUDIT, project).returncode, 0)

            card_preview = run_script(BUILD_CONTEXT, project)
            self.assertEqual(card_preview.returncode, 0, card_preview.stderr)
            self.assertIn("AIS-1 · версия 1", card_preview.stdout)
            self.assertIn("ANR-2: Предложить следующий этап проверки дома", card_preview.stdout)
            self.assertNotIn("ANR-1: Предложить следующий этап проверки дома", card_preview.stdout)
            card_applied = run_script(BUILD_CONTEXT, project, "--apply")
            self.assertEqual(card_applied.returncode, 0, card_applied.stderr)
            report = project / ".home-control" / "reports" / "project-context.md"
            self.assertTrue(report.is_file())
            report_text = report.read_text(encoding="utf-8")
            self.assertIn("Карточка объекта и продолжения работы", report_text)
            self.assertEqual(report_text, card_preview.stdout)

    def test_as_is_snapshot_rejects_document_without_complete_reading_and_extraction(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            project = Path(temporary) / "project"
            self.assertEqual(run_script(INIT, project).returncode, 0)
            document_path = project / "01_Обмеры_и_исходные_данные" / "inspection.txt"
            document_path.write_text("Фактическое состояние", encoding="utf-8")
            self.assertEqual(run_script(INDEX, project).returncode, 0)
            document = json.loads(
                (project / ".home-control" / "documents.json").read_text(encoding="utf-8")
            )["items"][0]
            current_version = max(document["versions"], key=lambda value: value["version"])
            control = project / ".home-control"
            fact = {
                "fact_id": "F-ASIS-DOC",
                "statement": "Документ содержит описание фактического состояния",
                "statement_kind": "source_fact",
                "evidence_origin": "actual_or_acceptance_document",
                "verification_status": "verified",
                "source_document_id": document["document_id"],
                "document_version": current_version["version"],
                "sha256": document["sha256"],
                "locator": "строка 1",
                "recorded_at": "2026-08-19",
                "discipline_ids": [],
                "package_ids": [],
                "site_ids": [],
                "zone_ids": [],
                "system_ids": [],
            }
            decision = {
                "decision_id": "D-ASIS-DOC",
                "decision_type": "as_is_snapshot_acceptance",
                "decision": "Использовать снимок",
                "status": "approved",
                "approved_by": "owner",
                "approved_at": "2026-08-19",
                "source_fact_ids": ["F-ASIS-DOC"],
            }
            snapshot = {
                "as_is_snapshot_id": "AIS-DOC",
                "snapshot_version": 1,
                "scope": "Объект",
                "captured_at": "2026-08-19",
                "owner_decision_id": "D-ASIS-DOC",
                "supersedes_as_is_snapshot_id": "",
                "document_versions": [{
                    "document_id": document["document_id"],
                    "document_version": current_version["version"],
                    "sha256": document["sha256"],
                }],
                "source_fact_ids": ["F-ASIS-DOC"],
                "information_gap_ids": [],
                "site_ids": [],
                "zone_ids": [],
                "physical_element_ids": [],
                "system_ids": [],
                "asset_ids": [],
                "route_ids": [],
                "asset_event_ids": [],
                "condition_assessment_ids": [],
                "limitations": [],
            }
            write_jsonl(control / "facts.jsonl", fact)
            write_jsonl(control / "decisions.jsonl", decision)
            write_jsonl(control / "as_is_snapshots.jsonl", snapshot)
            audited = run_script(AUDIT, project)
            self.assertEqual(audited.returncode, 1)
            self.assertIn("has no complete reading and fact extraction", audited.stdout)

    def test_regulatory_package_previews_validates_and_appends_atomically(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            project = Path(temporary) / "project"
            self.assertEqual(run_script(INIT, project).returncode, 0)
            control = project / ".home-control"
            fact = {
                "fact_id": "F-REG-1",
                "statement": "Владелец подтвердил тестовый объект нормативной проверки",
                "statement_kind": "source_fact",
                "evidence_origin": "owner_confirmation",
                "verification_status": "verified",
                "locator": "тестовая задача, сообщение владельца",
                "recorded_at": "2026-08-19",
                "discipline_ids": ["electrical"],
                "package_ids": [],
                "site_ids": [],
                "zone_ids": [],
                "system_ids": [],
            }
            write_jsonl(control / "facts.jsonl", fact)
            package = {
                "schema_version": "1.0",
                "norm_references": [{
                    "norm_reference_id": "NR-TEST-1",
                    "designation": "ГОСТ Р TEST-2026",
                    "title": "Тестовый нормативный документ",
                    "version": "2026",
                    "document_kind": "national_standard",
                    "document_status": "active",
                    "jurisdiction": "RU",
                    "territory": "Российская Федерация",
                    "checked_at": "2026-08-19",
                    "status_source_url": "https://example.test/status",
                    "source_url": "https://example.test/text",
                    "scope": "Тестовая электрическая установка",
                    "supersedes_norm_reference_ids": [],
                    "replacement_norm_reference_ids": [],
                }],
                "regulatory_requirements": [{
                    "regulatory_requirement_id": "RQR-TEST-1",
                    "norm_reference_id": "NR-TEST-1",
                    "locator": "пункт 1.1",
                    "statement": "Проверить тестовый параметр",
                    "scope_conditions": "Только для тестовой электрической установки",
                    "verification_method": "Документальная проверка",
                    "specialist_boundary": "Проектное решение подтверждает профильный специалист",
                    "source_url": "https://example.test/text#p1",
                    "extracted_at": "2026-08-19",
                    "verification_status": "verified",
                }],
                "compliance_assessments": [{
                    "compliance_assessment_id": "RCA-TEST-1",
                    "assessment_type": "project_solution",
                    "jurisdiction": "RU",
                    "scope": "Тестовая электрическая установка",
                    "assessed_at": "2026-08-19",
                    "status": "complete",
                    "target_entity_ids": ["F-REG-1"],
                    "norm_reference_ids": ["NR-TEST-1"],
                    "expected_requirement_ids": ["RQR-TEST-1"],
                    "checked_requirement_ids": ["RQR-TEST-1"],
                    "result_ids": ["RCR-TEST-1"],
                    "information_gap_ids": [],
                    "limitations": ["Тест не заменяет заключение профильного специалиста"],
                }],
                "compliance_results": [{
                    "compliance_result_id": "RCR-TEST-1",
                    "assessment_id": "RCA-TEST-1",
                    "requirement_id": "RQR-TEST-1",
                    "target_entity_ids": ["F-REG-1"],
                    "applicability_status": "applicable_by_project",
                    "compliance_status": "conforms",
                    "basis": "Подтверждено зарегистрированным фактом",
                    "checked_at": "2026-08-19",
                    "evidence_fact_ids": ["F-REG-1"],
                    "finding_ids": [],
                    "information_gap_ids": [],
                }],
                "regulatory_sync_runs": [],
            }
            path = project / "regulatory-package.json"
            path.write_text(json.dumps(package, ensure_ascii=False, indent=2), encoding="utf-8")
            target = control / "compliance_assessments.jsonl"
            before = target.read_bytes()

            preview = run_script(RECORD_REGULATORY, project, path)
            self.assertEqual(preview.returncode, 0, preview.stderr)
            self.assertEqual(target.read_bytes(), before)
            self.assertEqual(json.loads(preview.stdout)["append"]["compliance_assessments"], 1)

            applied = run_script(RECORD_REGULATORY, project, path, "--apply")
            self.assertEqual(applied.returncode, 0, applied.stderr)
            self.assertIn("RCA-TEST-1", target.read_text(encoding="utf-8"))
            self.assertEqual(run_script(AUDIT, project).returncode, 0)

            invalid = json.loads(json.dumps(package))
            invalid["norm_references"] = []
            invalid["regulatory_requirements"] = []
            invalid["compliance_results"] = []
            invalid["compliance_assessments"][0].update({
                "compliance_assessment_id": "RCA-TEST-BAD",
                "result_ids": [],
            })
            invalid_path = project / "invalid-regulatory-package.json"
            invalid_path.write_text(json.dumps(invalid, ensure_ascii=False), encoding="utf-8")
            rejected = run_script(RECORD_REGULATORY, project, invalid_path)
            self.assertEqual(rejected.returncode, 2)
            self.assertIn("one result per requirement", rejected.stderr)
            self.assertNotIn("RCA-TEST-BAD", target.read_text(encoding="utf-8"))

    def test_regulatory_source_checker_detects_content_change(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "official-source.html"
            source.write_text(
                "<html><body>version one<script>token-one</script></body></html>",
                encoding="utf-8",
            )
            catalog = root / "catalog.json"
            catalog.write_text(
                json.dumps({
                    "schema_version": "1.0",
                    "catalog_id": "test-regulatory-sources",
                    "jurisdiction": "RU",
                    "sources": [{
                        "source_id": "official-test",
                        "url": source.as_uri(),
                        "check_mode": "html_visible_text",
                    }],
                }, ensure_ascii=False),
                encoding="utf-8",
            )
            state = root / "state.json"
            report = root / "report.md"

            baseline = run_script(
                CHECK_REGULATORY_UPDATES,
                "--catalog", catalog,
                "--state", state,
                "--report", report,
                "--write-state",
            )
            self.assertEqual(baseline.returncode, 0, baseline.stderr)
            self.assertTrue(json.loads(baseline.stdout)["changed"])
            self.assertTrue(state.is_file())
            self.assertIn("baseline_created", report.read_text(encoding="utf-8"))

            unchanged = run_script(
                CHECK_REGULATORY_UPDATES,
                "--catalog", catalog,
                "--state", state,
                "--write-state",
            )
            self.assertEqual(unchanged.returncode, 0, unchanged.stderr)
            self.assertFalse(json.loads(unchanged.stdout)["changed"])

            source.write_text(
                "<html><body>version one<script>token-two</script></body></html>",
                encoding="utf-8",
            )
            script_only_change = run_script(
                CHECK_REGULATORY_UPDATES,
                "--catalog", catalog,
                "--state", state,
                "--write-state",
            )
            self.assertEqual(script_only_change.returncode, 0, script_only_change.stderr)
            self.assertFalse(json.loads(script_only_change.stdout)["changed"])

            source.write_text(
                "<html><body>version two<script>token-three</script></body></html>",
                encoding="utf-8",
            )
            changed = run_script(
                CHECK_REGULATORY_UPDATES,
                "--catalog", catalog,
                "--state", state,
                "--write-state",
            )
            self.assertEqual(changed.returncode, 0, changed.stderr)
            changed_result = json.loads(changed.stdout)
            self.assertTrue(changed_result["changed"])
            self.assertEqual(changed_result["source_checks"][0]["change_status"], "changed")

    def test_real_v1_through_v8_projects_migrate_without_losing_existing_data(self) -> None:
        for version in ("1.0", "2.0", "3.0", "4.0", "5.0", "6.0", "7.0", "8.0"):
            with self.subTest(version=version), tempfile.TemporaryDirectory() as temporary:
                project = Path(temporary) / "project"
                create_legacy_project(project, version)
                before = file_snapshot(project)
                original_marker = json.loads(before[".home-control/project.json"].decode("utf-8"))
                original_documents = json.loads(before[".home-control/documents.json"].decode("utf-8"))

                inspected = run_script(INSPECT, project)
                self.assertEqual(inspected.returncode, 2, inspected.stderr)
                self.assertEqual(json.loads(inspected.stdout)["status"], "project_migration_required")

                preview = run_script(REPAIR, project)
                self.assertEqual(preview.returncode, 0, preview.stderr)
                self.assertEqual(file_snapshot(project), before)
                self.assertIn("create_jsonl", preview.stdout)
                expected_added_registry = {
                    "1.0": "assets.jsonl",
                    "2.0": "assets.jsonl",
                    "3.0": "proposal_reviews.jsonl",
                    "4.0": "baseline_snapshots.jsonl",
                    "5.0": "project_packages.jsonl",
                    "6.0": "regulatory_requirements.jsonl",
                    "7.0": "document_intake_batches.jsonl",
                    "8.0": "cost_plans.jsonl",
                }[version]
                self.assertIn(expected_added_registry, preview.stdout)
                self.assertIn("update_project_marker_version", preview.stdout)
                if version == "1.0":
                    self.assertIn("migrate_json_schema_version", preview.stdout)
                else:
                    self.assertNotIn("migrate_json_schema_version", preview.stdout)

                applied = run_script(REPAIR, project, "--apply")
                self.assertEqual(applied.returncode, 0, applied.stderr)
                self.assertEqual(run_script(INSPECT, project).returncode, 0)

                after_marker = json.loads(
                    (project / ".home-control" / "project.json").read_text(encoding="utf-8")
                )
                after_documents = json.loads(
                    (project / ".home-control" / "documents.json").read_text(encoding="utf-8")
                )
                self.assertEqual(after_marker["created_by"]["structure_version"], STRUCTURE["structure_version"])
                self.assertEqual(after_marker["location"], original_marker["location"])
                self.assertEqual(after_marker["systems"], original_marker["systems"])
                self.assertEqual(after_documents["items"], original_documents["items"])
                self.assertEqual(after_documents["schema_version"], "2.0")

                for relative, content in before.items():
                    if relative in {".home-control/project.json", ".home-control/documents.json"}:
                        continue
                    self.assertEqual((project / relative).read_bytes(), content, relative)
                self.assertEqual((project / ".home-control" / "assets.jsonl").read_text(encoding="utf-8"), "")
                if version == "2.0":
                    self.assertIn(
                        "EO-LEGACY",
                        (project / ".home-control" / "equipment_options.jsonl").read_text(encoding="utf-8"),
                    )
                    self.assertNotIn(
                        "EO-LEGACY",
                        (project / ".home-control" / "assets.jsonl").read_text(encoding="utf-8"),
                    )
                marker_backups = list((project / ".home-control" / "recovery").rglob("project.json"))
                self.assertEqual(len(marker_backups), 1)
                self.assertEqual(marker_backups[0].read_bytes(), before[".home-control/project.json"])
                document_backups = list((project / ".home-control" / "recovery").rglob("documents.json"))
                self.assertEqual(len(document_backups), 1 if version == "1.0" else 0)
                if document_backups:
                    self.assertEqual(document_backups[0].read_bytes(), before[".home-control/documents.json"])

    def test_source_repository_is_not_a_user_project(self) -> None:
        inspected = run_script(INSPECT, REPO_ROOT)
        self.assertEqual(inspected.returncode, 2)
        self.assertEqual(json.loads(inspected.stdout)["status"], "plugin_source_workspace")
        refused = run_script(INIT, REPO_ROOT, "--dry-run")
        self.assertNotEqual(refused.returncode, 0)
        self.assertIn("inside the plugin distribution", refused.stderr)

    def test_unknown_older_and_newer_versions_are_blocked(self) -> None:
        for version, expected in (("1.5", "No supported migration"), ("10.0", "Refusing to downgrade")):
            with self.subTest(version=version), tempfile.TemporaryDirectory() as temporary:
                project = Path(temporary) / "project"
                self.assertEqual(run_script(INIT, project).returncode, 0)
                marker = project / ".home-control" / "project.json"
                value = json.loads(marker.read_text(encoding="utf-8"))
                value["created_by"]["structure_version"] = version
                marker.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
                before = marker.read_bytes()

                inspected = run_script(INSPECT, project)
                self.assertEqual(inspected.returncode, 2)
                expected_status = "project_migration_unsupported" if version == "1.5" else "project_created_by_newer_plugin"
                self.assertEqual(json.loads(inspected.stdout)["status"], expected_status)
                repaired = run_script(REPAIR, project)
                self.assertNotEqual(repaired.returncode, 0)
                self.assertIn(expected, repaired.stderr)
                self.assertEqual(marker.read_bytes(), before)

    def test_documents_schema_migration_must_match_the_source_structure_version(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            project = Path(temporary) / "project"
            create_legacy_project(project, "2.0")
            documents = project / ".home-control" / "documents.json"
            value = json.loads(documents.read_text(encoding="utf-8"))
            value["schema_version"] = "1.0"
            documents.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            before = file_snapshot(project)

            inspected = run_script(INSPECT, project)
            self.assertEqual(inspected.returncode, 2)
            self.assertEqual(json.loads(inspected.stdout)["status"], "project_structure_invalid")
            repaired = run_script(REPAIR, project, "--apply")
            self.assertEqual(repaired.returncode, 2, repaired.stderr)
            self.assertIn("unsupported schema migration", repaired.stdout)
            self.assertEqual(file_snapshot(project), before)

    def test_init_rejects_conflicting_or_unrecognized_managed_paths_before_writing(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            conflicting = base / "conflicting"
            conflicting.mkdir()
            managed_folder = conflicting / STRUCTURE["folders"][0]
            managed_folder.write_text("not a directory", encoding="utf-8")
            refused = run_script(INIT, conflicting)
            self.assertNotEqual(refused.returncode, 0)
            self.assertIn("not a directory", refused.stderr)
            self.assertFalse((conflicting / ".home-control").exists())

            unrecognized = base / "unrecognized"
            control = unrecognized / ".home-control"
            control.mkdir(parents=True)
            note = control / "existing.txt"
            note.write_text("preserve", encoding="utf-8")
            refused = run_script(INIT, unrecognized)
            self.assertNotEqual(refused.returncode, 0)
            self.assertIn("unrecognized non-empty", refused.stderr)
            self.assertEqual(note.read_text(encoding="utf-8"), "preserve")
            self.assertFalse((control / "project.json").exists())

    def test_missing_marker_identity_blocks_all_project_management(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            project = Path(temporary) / "project"
            self.assertEqual(run_script(INIT, project).returncode, 0)
            marker = project / ".home-control" / "project.json"
            value = json.loads(marker.read_text(encoding="utf-8"))
            del value["project_id"]
            marker.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            before = marker.read_bytes()

            inspected = run_script(INSPECT, project)
            self.assertEqual(inspected.returncode, 2)
            result = json.loads(inspected.stdout)
            self.assertEqual(result["status"], "project_structure_invalid")
            self.assertIn("project_id", " ".join(result["invalid"]))
            for script, arguments in (
                (INIT, (project, "--dry-run")),
                (REPAIR, (project, "--apply")),
                (INDEX, (project, "--dry-run")),
                (
                    INGEST,
                    (
                        project,
                        "--source",
                        project / "source.pdf",
                        "--target-folder",
                        "02_Проекты_и_технические_решения",
                        "--description",
                        "Проект",
                        "--apply",
                    ),
                ),
            ):
                with self.subTest(script=script.name):
                    self.assertNotEqual(run_script(script, *arguments).returncode, 0)
                    self.assertEqual(marker.read_bytes(), before)

    def test_dangling_managed_link_is_blocked_without_writing_its_target(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            project = base / "project"
            self.assertEqual(run_script(INIT, project).returncode, 0)
            registry = project / ".home-control" / "assets.jsonl"
            registry.unlink()
            external = base / "outside" / "assets.jsonl"
            external.parent.mkdir()
            try:
                os.symlink(external, registry)
            except (NotImplementedError, OSError) as exc:
                self.skipTest(f"symbolic links are unavailable: {exc}")

            inspected = run_script(INSPECT, project)
            self.assertEqual(inspected.returncode, 2)
            self.assertEqual(json.loads(inspected.stdout)["status"], "project_structure_invalid")
            self.assertNotEqual(run_script(INIT, project).returncode, 0)
            repaired = run_script(REPAIR, project, "--apply")
            self.assertEqual(repaired.returncode, 2, repaired.stderr)
            self.assertFalse(external.exists())
            self.assertTrue(registry.is_symlink())

    def test_repair_rejects_an_unsafe_recovery_path_before_backup(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            project = base / "project"
            self.assertEqual(run_script(INIT, project).returncode, 0)
            costs = project / ".home-control" / "costs.csv"
            with costs.open("r", encoding="utf-8-sig", newline="") as handle:
                headers = next(csv.reader(handle))
            with costs.open("w", encoding="utf-8-sig", newline="") as handle:
                writer = csv.writer(handle)
                writer.writerow(headers[:-1])
                writer.writerow(["C-1", *("" for _ in headers[2:])])

            recovery = project / ".home-control" / "recovery"
            outside = base / "outside"
            outside.mkdir()
            try:
                os.symlink(outside, recovery, target_is_directory=True)
            except (NotImplementedError, OSError):
                recovery.write_text("not a directory", encoding="utf-8")
            before = costs.read_bytes()

            repaired = run_script(REPAIR, project, "--apply")
            self.assertNotEqual(repaired.returncode, 0)
            self.assertIn("recovery", repaired.stderr.lower())
            self.assertEqual(costs.read_bytes(), before)
            self.assertEqual(list(outside.rglob("*")), [])

    def test_init_and_consumers_refuse_a_legacy_project_before_migration(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            project = Path(temporary) / "project"
            create_legacy_project(project, "2.0")
            for script, arguments in (
                (INIT, (project, "--dry-run")),
                (INDEX, (project, "--dry-run")),
                (
                    INGEST,
                    (
                        project,
                        "--source",
                        project / "source.pdf",
                        "--target-folder",
                        "02_Проекты_и_технические_решения",
                        "--description",
                        "Проект",
                        "--apply",
                    ),
                ),
                (AUDIT, (project,)),
                (DASHBOARD, (project,)),
            ):
                with self.subTest(script=script.name):
                    result = run_script(script, *arguments)
                    self.assertNotEqual(result.returncode, 0)
                    self.assertIn("project_migration_required", result.stderr)

    def test_init_can_restore_only_a_missing_item_in_a_current_project(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            project = Path(temporary) / "project"
            self.assertEqual(run_script(INIT, project).returncode, 0)
            registry = project / ".home-control" / "assets.jsonl"
            registry.unlink()

            preview = run_script(INIT, project, "--dry-run")
            self.assertEqual(preview.returncode, 0, preview.stderr)
            self.assertIn("assets.jsonl", preview.stdout)
            self.assertFalse(registry.exists())
            applied = run_script(INIT, project)
            self.assertEqual(applied.returncode, 0, applied.stderr)
            self.assertTrue(registry.is_file())
            self.assertEqual(run_script(INSPECT, project).returncode, 0)

    def test_index_preserves_registry_metadata_and_blocks_duplicate_document_ids(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            project = Path(temporary) / "project"
            self.assertEqual(run_script(INIT, project).returncode, 0)
            source = project / "01_Обмеры_и_исходные_данные" / "source.txt"
            source.write_text("data", encoding="utf-8")
            registry = project / ".home-control" / "documents.json"
            documents = json.loads(registry.read_text(encoding="utf-8"))
            documents["custom_metadata"] = {"preserve": True}
            registry.write_text(json.dumps(documents, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

            indexed = run_script(INDEX, project)
            self.assertEqual(indexed.returncode, 0, indexed.stderr)
            documents = json.loads(registry.read_text(encoding="utf-8"))
            self.assertEqual(documents["custom_metadata"], {"preserve": True})
            duplicate = dict(documents["items"][0])
            duplicate["relative_path"] = "another.txt"
            documents["items"].append(duplicate)
            registry.write_text(json.dumps(documents, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            before = registry.read_bytes()

            refused = run_script(INDEX, project, "--dry-run")
            self.assertNotEqual(refused.returncode, 0)
            self.assertIn("Duplicate document_id", refused.stderr)
            self.assertEqual(registry.read_bytes(), before)

    def test_ingest_previews_then_copies_indexes_and_records_user_context(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            project = base / "project"
            source = base / "Проект отопления.pdf"
            source.write_bytes(b"approved heating project")
            self.assertEqual(run_script(INIT, project).returncode, 0)
            registry = project / ".home-control" / "documents.json"
            registry_before = registry.read_bytes()
            target_folder = "02_Проекты_и_технические_решения"
            description = "Утверждённый владельцем проект отопления"

            preview = run_script(
                INGEST,
                project,
                "--source",
                source,
                "--target-folder",
                target_folder,
                "--description",
                description,
            )
            self.assertEqual(preview.returncode, 0, preview.stderr)
            preview_data = json.loads(preview.stdout)
            self.assertEqual(preview_data["mode"], "preview")
            self.assertEqual(preview_data["items"][0]["action"], "copy")
            destination = project / target_folder / source.name
            self.assertFalse(destination.exists())
            self.assertEqual(registry.read_bytes(), registry_before)
            self.assertEqual(source.read_bytes(), b"approved heating project")

            applied = run_script(
                INGEST,
                project,
                "--source",
                source,
                "--target-folder",
                target_folder,
                "--description",
                description,
                "--apply",
            )
            self.assertEqual(applied.returncode, 0, applied.stderr)
            self.assertEqual(json.loads(applied.stdout)["mode"], "applied")
            self.assertEqual(destination.read_bytes(), source.read_bytes())
            documents = json.loads(registry.read_text(encoding="utf-8"))
            document = next(
                item
                for item in documents["items"]
                if item["relative_path"] == f"{target_folder}/{source.name}"
            )
            self.assertEqual(document["status"], "active")
            self.assertEqual(len(document["intake_contexts"]), 1)
            self.assertEqual(document["intake_contexts"][0]["description"], description)
            self.assertEqual(document["intake_contexts"][0]["declared_by"], "user")
            self.assertEqual(document["intake_contexts"][0]["verification_status"], "unreviewed")
            batch_id = document["intake_contexts"][0]["intake_batch_id"]
            batches = read_jsonl(project / ".home-control" / "document_intake_batches.jsonl")
            self.assertEqual(len(batches), 1)
            self.assertEqual(batches[0]["intake_batch_id"], batch_id)
            self.assertEqual(batches[0]["items"][0]["document_id"], document["document_id"])

            repeated = run_script(
                INGEST,
                project,
                "--source",
                source,
                "--target-folder",
                target_folder,
                "--description",
                description,
                "--apply",
            )
            self.assertEqual(repeated.returncode, 0, repeated.stderr)
            self.assertEqual(json.loads(repeated.stdout)["items"][0]["action"], "use_existing_identical")
            documents = json.loads(registry.read_text(encoding="utf-8"))
            document = next(
                item
                for item in documents["items"]
                if item["relative_path"] == f"{target_folder}/{source.name}"
            )
            self.assertEqual(len(document["intake_contexts"]), 1)
            self.assertEqual(
                len(read_jsonl(project / ".home-control" / "document_intake_batches.jsonl")),
                1,
            )

    def test_heterogeneous_intake_manifest_uses_one_preview_and_one_atomic_batch(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            project = base / "project"
            design = base / "Проект.pdf"
            quote = base / "КП.xlsx"
            design.write_bytes(b"design")
            quote.write_bytes(b"quote")
            self.assertEqual(run_script(INIT, project).returncode, 0)
            manifest = {
                "schema_version": "1.0",
                "batch_description": "Первичный пакет документов объекта",
                "items": [
                    {
                        "source": str(design),
                        "target_folder": "02_Проекты_и_технические_решения",
                        "description": "Проектное решение, статус предстоит проверить",
                    },
                    {
                        "source": str(quote),
                        "target_folder": "03_Коммерческие_предложения",
                        "description": "Коммерческое предложение подрядчика",
                    },
                ],
            }
            manifest_path = base / "intake.json"
            manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
            documents_path = project / ".home-control" / "documents.json"
            before = documents_path.read_bytes()

            preview = run_script(INGEST, project, "--manifest", manifest_path)
            self.assertEqual(preview.returncode, 0, preview.stderr)
            preview_data = json.loads(preview.stdout)
            self.assertEqual(preview_data["mode"], "preview")
            self.assertEqual(len(preview_data["items"]), 2)
            self.assertTrue(preview_data["intake_batch_id"].startswith("INT-"))
            self.assertEqual(documents_path.read_bytes(), before)

            wrong_plan = run_script(
                INGEST,
                project,
                "--manifest",
                manifest_path,
                "--expected-batch-id",
                "INT-not-the-approved-plan",
                "--apply",
            )
            self.assertEqual(wrong_plan.returncode, 2)
            self.assertEqual(documents_path.read_bytes(), before)
            self.assertFalse((project / "02_Проекты_и_технические_решения" / design.name).exists())

            applied = run_script(
                INGEST,
                project,
                "--manifest",
                manifest_path,
                "--expected-batch-id",
                preview_data["intake_batch_id"],
                "--apply",
            )
            self.assertEqual(applied.returncode, 0, applied.stderr)
            self.assertTrue((project / "02_Проекты_и_технические_решения" / design.name).is_file())
            self.assertTrue((project / "03_Коммерческие_предложения" / quote.name).is_file())
            batches = read_jsonl(project / ".home-control" / "document_intake_batches.jsonl")
            self.assertEqual(len(batches), 1)
            self.assertEqual(len(batches[0]["items"]), 2)
            self.assertEqual(run_script(AUDIT, project).returncode, 0)

    def test_ingest_blocks_name_collision_without_overwriting_either_file(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            project = base / "project"
            source = base / "offer.pdf"
            source.write_bytes(b"new offer")
            self.assertEqual(run_script(INIT, project).returncode, 0)
            destination = project / "03_Коммерческие_предложения" / source.name
            destination.write_bytes(b"existing offer")
            registry = project / ".home-control" / "documents.json"
            registry_before = registry.read_bytes()

            refused = run_script(
                INGEST,
                project,
                "--source",
                source,
                "--target-folder",
                "03_Коммерческие_предложения",
                "--description",
                "КП подрядчика",
                "--apply",
            )
            self.assertEqual(refused.returncode, 2)
            self.assertIn("different file", refused.stderr)
            self.assertEqual(source.read_bytes(), b"new offer")
            self.assertEqual(destination.read_bytes(), b"existing offer")
            self.assertEqual(registry.read_bytes(), registry_before)

    def test_ingest_does_not_silently_relocate_a_document_already_in_project(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            project = Path(temporary) / "project"
            self.assertEqual(run_script(INIT, project).returncode, 0)
            source = project / "document.pdf"
            source.write_bytes(b"unsorted")

            refused = run_script(
                INGEST,
                project,
                "--source",
                source,
                "--target-folder",
                "02_Проекты_и_технические_решения",
                "--description",
                "Проект",
                "--apply",
            )
            self.assertEqual(refused.returncode, 2)
            self.assertIn("already inside this project", refused.stderr)
            self.assertTrue(source.is_file())
            self.assertFalse(
                (project / "02_Проекты_и_технические_решения" / source.name).exists()
            )

    def test_invalid_nonempty_jsonl_blocks_migration_without_data_loss(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            project = Path(temporary) / "project"
            create_legacy_project(project, "2.0")
            registry = project / ".home-control" / "facts.jsonl"
            registry.write_text(registry.read_text(encoding="utf-8") + "{broken\n", encoding="utf-8")
            before = registry.read_bytes()
            marker_before = (project / ".home-control" / "project.json").read_bytes()

            preview = run_script(REPAIR, project)
            self.assertEqual(preview.returncode, 2, preview.stderr)
            self.assertIn("automatic replacement is forbidden", preview.stdout)
            self.assertEqual(registry.read_bytes(), before)
            self.assertEqual((project / ".home-control" / "project.json").read_bytes(), marker_before)

            applied = run_script(REPAIR, project, "--apply")
            self.assertEqual(applied.returncode, 2, applied.stderr)
            self.assertEqual(registry.read_bytes(), before)
            self.assertEqual((project / ".home-control" / "project.json").read_bytes(), marker_before)

    def test_csv_header_repair_preserves_rows_and_uses_unique_backups(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            project = Path(temporary) / "project"
            self.assertEqual(run_script(INIT, project).returncode, 0)
            targets = [
                project / ".home-control" / "costs.csv",
                project / ".home-control" / "issues.csv",
            ]
            for index, target in enumerate(targets, 1):
                with target.open("r", encoding="utf-8-sig", newline="") as handle:
                    headers = next(csv.reader(handle))
                retained_headers = headers[:-1]
                with target.open("w", encoding="utf-8-sig", newline="") as handle:
                    writer = csv.DictWriter(handle, fieldnames=retained_headers)
                    writer.writeheader()
                    writer.writerow({retained_headers[0]: f"LEGACY-{index}"})

                applied = run_script(REPAIR, project, "--apply")
                self.assertEqual(applied.returncode, 0, applied.stderr)
                with target.open("r", encoding="utf-8-sig", newline="") as handle:
                    rows = list(csv.DictReader(handle))
                self.assertEqual(rows[0][retained_headers[0]], f"LEGACY-{index}")
                self.assertIn(headers[-1], rows[0])

            recovery = project / ".home-control" / "recovery"
            backup_runs = [path for path in recovery.iterdir() if path.is_dir()]
            self.assertEqual(len(backup_runs), 2)
            self.assertEqual(len({path.name for path in backup_runs}), 2)

    def test_short_csv_rows_block_gate_and_repair_without_mutation(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            project = Path(temporary) / "project"
            self.assertEqual(run_script(INIT, project).returncode, 0)
            costs = project / ".home-control" / "costs.csv"
            with costs.open("r", encoding="utf-8-sig", newline="") as handle:
                headers = next(csv.reader(handle))
            with costs.open("w", encoding="utf-8-sig", newline="") as handle:
                writer = csv.writer(handle)
                writer.writerow(headers)
                writer.writerow(["C-SHORT"])
            before = costs.read_bytes()

            inspected = run_script(INSPECT, project)
            self.assertEqual(inspected.returncode, 2)
            result = json.loads(inspected.stdout)
            self.assertEqual(result["status"], "project_structure_invalid")
            self.assertIn("ambiguous columns or row widths", " ".join(result["invalid"]))
            repaired = run_script(REPAIR, project, "--apply")
            self.assertEqual(repaired.returncode, 2, repaired.stderr)
            self.assertIn("ambiguous CSV columns or row widths", repaired.stdout)
            self.assertEqual(costs.read_bytes(), before)
            self.assertNotEqual(run_script(DASHBOARD, project).returncode, 0)

    def test_audit_detects_zone_cycles_and_cross_site_asset_links(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            project = Path(temporary) / "project"
            self.assertEqual(run_script(INIT, project).returncode, 0)
            marker = json.loads((project / ".home-control" / "project.json").read_text(encoding="utf-8"))
            control = project / ".home-control"
            write_jsonl(
                control / "sites.jsonl",
                {"site_id": "SITE-A", "project_id": marker["project_id"], "site_kind": "building", "status": "active"},
                {"site_id": "SITE-B", "project_id": marker["project_id"], "site_kind": "building", "status": "active"},
            )
            write_jsonl(
                control / "zones.jsonl",
                {"zone_id": "ZONE-A", "site_id": "SITE-A", "parent_zone_id": "ZONE-B", "zone_kind": "room"},
                {"zone_id": "ZONE-B", "site_id": "SITE-B", "parent_zone_id": "ZONE-A", "zone_kind": "room"},
            )
            write_jsonl(
                control / "assets.jsonl",
                {
                    "asset_id": "AST-1",
                    "site_id": "SITE-A",
                    "zone_id": "ZONE-B",
                    "lifecycle_status": "installed",
                    "operational_status": "active",
                },
            )

            audited = run_script(AUDIT, project)
            self.assertEqual(audited.returncode, 1, audited.stderr)
            self.assertIn("parent zone belongs to a different site", audited.stdout)
            self.assertIn("parent cycle detected", audited.stdout)
            self.assertIn("asset site_id conflicts with zone site_id", audited.stdout)

    def test_valid_site_zone_system_asset_graph_passes_audit(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            project = Path(temporary) / "project"
            self.assertEqual(run_script(INIT, project).returncode, 0)
            marker = json.loads((project / ".home-control" / "project.json").read_text(encoding="utf-8"))
            control = project / ".home-control"
            write_jsonl(
                control / "facts.jsonl",
                {
                    "fact_id": "F-1",
                    "statement": "Физический граф подтверждён владельцем для теста",
                    "statement_kind": "source_fact",
                    "evidence_origin": "owner_confirmation",
                    "verification_status": "verified",
                    "locator": "тестовая задача, сообщение владельца",
                    "recorded_at": "2026-08-19",
                    "discipline_ids": [],
                    "package_ids": [],
                    "site_ids": [],
                    "zone_ids": [],
                    "system_ids": [],
                },
            )
            write_jsonl(
                control / "sites.jsonl",
                {
                    "site_id": "SITE-1",
                    "project_id": marker["project_id"],
                    "name": "Тестовый объект",
                    "site_kind": "building",
                    "status": "active",
                    "source_fact_ids": ["F-1"],
                    "verification_status": "verified",
                },
            )
            write_jsonl(
                control / "zones.jsonl",
                {
                    "zone_id": "ZONE-1",
                    "site_id": "SITE-1",
                    "name": "Техническое помещение",
                    "zone_kind": "equipment_room",
                    "source_fact_ids": ["F-1"],
                    "verification_status": "verified",
                },
            )
            write_jsonl(
                control / "systems.jsonl",
                {
                    "system_id": "SYS-1",
                    "site_ids": ["SITE-1"],
                    "name": "Тестовая система",
                    "function": "Контроль",
                    "operational_status": "active",
                    "source_fact_ids": ["F-1"],
                    "verification_status": "verified",
                },
            )
            write_jsonl(
                control / "assets.jsonl",
                {
                    "asset_id": "AST-1",
                    "site_id": "SITE-1",
                    "zone_id": "ZONE-1",
                    "asset_type": "controller",
                    "name": "Тестовый контроллер",
                    "system_ids": ["SYS-1"],
                    "lifecycle_status": "installed",
                    "operational_status": "active",
                    "source_fact_ids": ["F-1"],
                    "verification_status": "verified",
                },
                {
                    "asset_id": "AST-STORED",
                    "site_id": "SITE-1",
                    "asset_type": "spare_controller",
                    "name": "Учтённый запасной контроллер",
                    "system_ids": [],
                    "lifecycle_status": "stored",
                    "operational_status": "offline",
                    "source_fact_ids": ["F-1"],
                    "verification_status": "verified",
                },
            )
            write_jsonl(
                control / "routes.jsonl",
                {
                    "route_id": "RTE-1",
                    "site_id": "SITE-1",
                    "zone_ids": ["ZONE-1"],
                    "system_ids": ["SYS-1"],
                    "route_type": "data_cable",
                    "locator": "Техническое помещение, открытая трасса",
                    "source_fact_ids": ["F-1"],
                    "verification_status": "verified",
                },
            )
            write_jsonl(
                control / "condition_assessments.jsonl",
                {
                    "condition_assessment_id": "CA-1",
                    "target_entity_id": "RTE-1",
                    "method": "visual_inspection",
                    "condition_status": "observed_serviceable",
                    "source_fact_ids": ["F-1"],
                    "verification_status": "verified",
                },
            )
            write_jsonl(
                control / "maintenance_plans.jsonl",
                {
                    "maintenance_plan_id": "MP-1",
                    "target_entity_ids": ["RTE-1"],
                    "operation": "Проверить состояние трассы",
                    "status": "active",
                    "trigger_type": "manual",
                    "basis_fact_ids": ["F-1"],
                    "verification_status": "verified",
                },
            )
            write_jsonl(
                control / "work_requests.jsonl",
                {
                    "work_request_id": "WR-1",
                    "target_entity_ids": ["RTE-1"],
                    "request_type": "inspect",
                    "goal": "Подтвердить состояние трассы",
                    "status": "draft",
                    "source_fact_ids": ["F-1"],
                    "verification_status": "verified",
                },
            )
            write_jsonl(
                control / "equipment_options.jsonl",
                {
                    "equipment_option_id": "EO-1",
                    "work_request_id": "WR-1",
                    "replaces_asset_ids": ["AST-1"],
                    "status": "identified",
                },
            )
            write_jsonl(
                control / "project_packages.jsonl",
                {
                    "package_id": "PKG-A",
                    "name": "Пакет автоматики",
                    "goal": "Обновить контроллер",
                    "status": "coordinating",
                    "disciplines": ["automation", "electrical"],
                    "source_document_versions": [],
                    "fact_ids": ["F-1"],
                    "requirement_ids": [],
                    "information_gap_ids": ["GAP-1"],
                    "site_ids": ["SITE-1"],
                    "zone_ids": ["ZONE-1"],
                    "system_ids": ["SYS-1"],
                },
                {
                    "package_id": "PKG-B",
                    "name": "Пакет электропитания",
                    "goal": "Обеспечить питание оборудования",
                    "status": "coordinating",
                    "disciplines": ["electrical"],
                    "source_document_versions": [],
                    "fact_ids": ["F-1"],
                    "requirement_ids": [],
                    "information_gap_ids": [],
                    "site_ids": ["SITE-1"],
                    "zone_ids": ["ZONE-1"],
                    "system_ids": ["SYS-1"],
                },
            )
            write_jsonl(
                control / "information_gaps.jsonl",
                {
                    "gap_id": "GAP-1",
                    "description": "Не подтверждена требуемая мощность контроллера",
                    "blocked_conclusion": "Достаточность электрической мощности",
                    "required_provider": "производитель оборудования",
                    "required_format": "официальный паспорт",
                    "status": "requested",
                    "package_ids": ["PKG-A"],
                    "blocked_entity_ids": ["RDM-A"],
                    "answer_source_ids": [],
                },
            )
            write_jsonl(
                control / "shared_resources.jsonl",
                {
                    "resource_id": "RES-POWER",
                    "name": "Доступная электрическая мощность",
                    "resource_type": "electrical_capacity",
                    "site_id": "SITE-1",
                    "zone_ids": ["ZONE-1"],
                    "source_fact_ids": ["F-1"],
                },
            )
            write_jsonl(
                control / "resource_demands.jsonl",
                {
                    "demand_id": "RDM-A",
                    "package_id": "PKG-A",
                    "resource_id": "RES-POWER",
                    "description": "Мощность нового контроллера",
                    "status": "candidate",
                    "source_fact_ids": [],
                    "information_gap_ids": ["GAP-1"],
                },
                {
                    "demand_id": "RDM-B",
                    "package_id": "PKG-B",
                    "resource_id": "RES-POWER",
                    "description": "Резерв питания для автоматики",
                    "status": "confirmed",
                    "source_fact_ids": ["F-1"],
                    "information_gap_ids": [],
                },
            )
            write_jsonl(
                control / "package_interfaces.jsonl",
                {
                    "package_interface_id": "PIF-1",
                    "package_ids": ["PKG-A", "PKG-B"],
                    "interface_type": "shared_resource",
                    "description": "Оба пакета используют общий резерв мощности",
                    "resource_ids": ["RES-POWER"],
                    "source_fact_ids": ["F-1"],
                },
            )
            write_jsonl(
                control / "coordination_issues.jsonl",
                {
                    "coordination_issue_id": "CI-1",
                    "package_ids": ["PKG-A", "PKG-B"],
                    "issue_type": "capacity_shortfall",
                    "status": "under_review",
                    "description": "Достаточность общего резерва пока не подтверждена",
                    "resource_ids": ["RES-POWER"],
                    "source_fact_ids": ["F-1"],
                    "information_gap_ids": ["GAP-1"],
                },
            )
            write_jsonl(
                control / "coordination_runs.jsonl",
                {
                    "coordination_run_id": "CR-1",
                    "status": "complete",
                    "package_ids": ["PKG-A", "PKG-B"],
                    "expected_package_pairs": [["PKG-A", "PKG-B"]],
                    "checked_package_pairs": [["PKG-B", "PKG-A"]],
                    "resource_demand_ids": ["RDM-A", "RDM-B"],
                    "issue_ids": ["CI-1"],
                    "coverage_gaps": [],
                },
            )

            audited = run_script(AUDIT, project)
            self.assertEqual(audited.returncode, 0, audited.stdout + audited.stderr)

            write_jsonl(
                control / "coordination_runs.jsonl",
                {
                    "coordination_run_id": "CR-1",
                    "status": "complete",
                    "package_ids": ["PKG-A", "PKG-B"],
                    "expected_package_pairs": [["PKG-A", "PKG-B"]],
                    "checked_package_pairs": [],
                    "resource_demand_ids": ["RDM-A", "RDM-B"],
                    "issue_ids": ["CI-1"],
                    "coverage_gaps": [],
                },
            )
            invalid_coordination = run_script(AUDIT, project)
            self.assertEqual(invalid_coordination.returncode, 1, invalid_coordination.stderr)
            self.assertIn("complete coordination run has unresolved", invalid_coordination.stdout)

    def test_audit_rejects_physical_records_without_provenance(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            project = Path(temporary) / "project"
            self.assertEqual(run_script(INIT, project).returncode, 0)
            marker = json.loads((project / ".home-control" / "project.json").read_text(encoding="utf-8"))
            control = project / ".home-control"
            write_jsonl(
                control / "sites.jsonl",
                {
                    "site_id": "SITE-UNPROVEN",
                    "project_id": marker["project_id"],
                    "name": "Неподтверждённый объект",
                    "site_kind": "building",
                    "status": "active",
                },
            )

            audited = run_script(AUDIT, project)
            self.assertEqual(audited.returncode, 1, audited.stderr)
            self.assertIn("missing or empty required array source_fact_ids", audited.stdout)
            self.assertIn("missing or unknown verification_status", audited.stdout)

    def test_audit_enforces_lifecycle_statuses_links_and_owner_authorization(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            project = Path(temporary) / "project"
            self.assertEqual(run_script(INIT, project).returncode, 0)
            marker = json.loads((project / ".home-control" / "project.json").read_text(encoding="utf-8"))
            control = project / ".home-control"
            write_jsonl(
                control / "sites.jsonl",
                {"site_id": "SITE-1", "project_id": marker["project_id"], "site_kind": "building", "status": "active"},
            )
            write_jsonl(
                control / "decisions.jsonl",
                {"decision_id": "D-BAD", "status": "not-a-status"},
            )
            write_jsonl(
                control / "work_requests.jsonl",
                {
                    "work_request_id": "WR-1",
                    "target_entity_ids": ["SITE-1"],
                    "request_type": "replace",
                    "status": "approved",
                },
            )
            write_jsonl(
                control / "equipment_options.jsonl",
                {"equipment_option_id": "EO-1", "work_request_id": "WR-1", "status": "selected"},
            )
            write_jsonl(
                control / "maintenance_plans.jsonl",
                {
                    "maintenance_plan_id": "MP-1",
                    "target_entity_ids": ["SITE-1"],
                    "status": "not-a-status",
                    "trigger_type": "not-a-trigger",
                },
            )
            write_jsonl(
                control / "lifecycle_cost_assessments.jsonl",
                {
                    "lifecycle_cost_assessment_id": "LCA-1",
                    "work_request_id": "WR-1",
                    "status": "not-a-status",
                    "components": [{"component_type": "not-a-component"}],
                    "scenario_ids": ["SCENARIO-1"],
                },
            )
            write_jsonl(
                control / "lifecycle_decisions.jsonl",
                {
                    "lifecycle_decision_id": "LD-1",
                    "work_request_id": "WR-1",
                    "status": "implemented",
                    "action": "replace",
                    "lifecycle_cost_assessment_ids": ["LCA-1"],
                },
            )
            write_jsonl(control / "contractors.jsonl", {"contractor_id": "CTR-1"})
            write_jsonl(control / "suppliers.jsonl", {"supplier_id": "SUP-1"})
            write_jsonl(
                control / "quotes.jsonl",
                {
                    "quote_id": "Q-1",
                    "source_document_id": "DOC-MISSING",
                    "contractor_id": "CTR-1",
                    "supplier_id": "SUP-1",
                },
            )
            write_jsonl(
                control / "price_observations.jsonl",
                {
                    "price_observation_id": "PO-1",
                    "subject_entity_id": "UNKNOWN-1",
                    "seller_contractor_id": "CTR-1",
                    "seller_supplier_id": "SUP-1",
                },
            )
            write_jsonl(
                control / "comparables.jsonl",
                {"comparable_id": "CMP-1", "status": "confirmed_relevant", "source_url": "https://example.test"},
            )

            audited = run_script(AUDIT, project)
            self.assertEqual(audited.returncode, 1, audited.stderr)
            for message in (
                "unknown owner decision status",
                "approved-or-later request has no owner decision",
                "selected option has no owner decision",
                "missing or unknown target_entity_ids",
                "unknown maintenance plan status",
                "unknown maintenance trigger type",
                "unknown lifecycle cost status",
                "unknown component_type",
                "owner-decided lifecycle action has no owner decision",
                "implemented decision has no implemented_event_ids",
                "quote must have exactly one contractor_id or supplier_id",
                "seller must not be both a supplier and a contractor",
                "unknown subject_entity_id",
                "confirmed comparable has no confirmation fact",
            ):
                with self.subTest(message=message):
                    self.assertIn(message, audited.stdout)

    def test_audit_rejects_false_complete_reading_run(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            project = Path(temporary) / "project"
            self.assertEqual(run_script(INIT, project).returncode, 0)
            source = project / "01_Обмеры_и_исходные_данные" / "source.txt"
            source.write_text("data", encoding="utf-8")
            self.assertEqual(run_script(INDEX, project).returncode, 0)
            documents = json.loads((project / ".home-control" / "documents.json").read_text(encoding="utf-8"))
            document = documents["items"][0]
            reading_run = {
                "reading_run_id": "RR-0001",
                "source_document_id": document["document_id"],
                "document_version": 1,
                "sha256": document["sha256"],
                "status": "complete",
                "coverage": {"expected_units": [1], "checked_units": [], "gaps": []},
                "summary_path": ".home-control/summaries/missing.md",
            }
            registry = project / ".home-control" / "reading_runs.jsonl"
            registry.write_text(json.dumps(reading_run, ensure_ascii=False) + "\n", encoding="utf-8")
            audited = run_script(AUDIT, project)
            self.assertEqual(audited.returncode, 1)
            self.assertIn("complete run has unresolved or inconsistent coverage", audited.stdout)
            self.assertIn("complete run has no existing summary file", audited.stdout)

    def test_audit_rejects_non_array_complete_coverage(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            project = Path(temporary) / "project"
            self.assertEqual(run_script(INIT, project).returncode, 0)
            source = project / "01_Обмеры_и_исходные_данные" / "source.txt"
            source.write_text("data", encoding="utf-8")
            self.assertEqual(run_script(INDEX, project).returncode, 0)
            documents = json.loads((project / ".home-control" / "documents.json").read_text(encoding="utf-8"))
            document = documents["items"][0]
            summary = project / ".home-control" / "summaries" / "source-v1.md"
            summary.write_text("# Конспект\n", encoding="utf-8")
            reading_run = {
                "reading_run_id": "RR-STRING-COVERAGE",
                "source_document_id": document["document_id"],
                "document_version": 1,
                "sha256": document["sha256"],
                "status": "complete",
                "coverage": {"expected_units": "page-1", "checked_units": "page-1", "gaps": []},
                "summary_path": ".home-control/summaries/source-v1.md",
            }
            registry = project / ".home-control" / "reading_runs.jsonl"
            registry.write_text(json.dumps(reading_run, ensure_ascii=False) + "\n", encoding="utf-8")

            audited = run_script(AUDIT, project)
            self.assertEqual(audited.returncode, 1, audited.stderr)
            self.assertIn("complete run has unresolved or inconsistent coverage", audited.stdout)
            self.assertNotIn("complete run has no existing summary file", audited.stdout)

    def test_document_inventory_previews_applies_and_is_idempotent(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            project = Path(temporary) / "project"
            self.assertEqual(run_script(INIT, project).returncode, 0)
            relative = Path("01_Обмеры_и_исходные_данные") / "source.txt"
            (project / relative).write_text("первая\nвторая\nтретья\n", encoding="utf-8")
            self.assertEqual(run_script(INDEX, project).returncode, 0)

            preview = run_script(INVENTORY, project, relative.as_posix())
            self.assertEqual(preview.returncode, 0, preview.stderr)
            self.assertEqual(json.loads(preview.stdout)["inventory"]["expected_units"], [1, 2, 3])
            registry = project / ".home-control" / "document_inventories.jsonl"
            self.assertEqual(registry.read_text(encoding="utf-8"), "")

            applied = run_script(INVENTORY, project, relative.as_posix(), "--apply")
            self.assertEqual(applied.returncode, 0, applied.stderr)
            self.assertTrue(json.loads(applied.stdout)["appended"])
            first_record = json.loads(registry.read_text(encoding="utf-8"))
            repeated = run_script(INVENTORY, project, relative.as_posix(), "--apply")
            self.assertEqual(repeated.returncode, 0, repeated.stderr)
            self.assertFalse(json.loads(repeated.stdout)["appended"])
            self.assertEqual(json.loads(registry.read_text(encoding="utf-8")), first_record)

    def test_workbook_inventory_detects_hidden_content_and_formulas(self) -> None:
        try:
            from openpyxl import Workbook
            from openpyxl.comments import Comment
        except ImportError:
            self.skipTest("openpyxl is unavailable")
        with tempfile.TemporaryDirectory() as temporary:
            project = Path(temporary) / "project"
            self.assertEqual(run_script(INIT, project).returncode, 0)
            relative = Path("02_Проекты_и_технические_решения") / "offer.xlsx"
            workbook = Workbook()
            sheet = workbook.active
            sheet.title = "Смета"
            sheet["A1"] = 2
            sheet["B1"] = 100
            sheet["C1"] = "=A1*B1"
            sheet["A2"].comment = Comment("проверить", "тест")
            sheet.row_dimensions[2].hidden = True
            hidden = workbook.create_sheet("Скрытые данные")
            hidden.sheet_state = "hidden"
            hidden["A1"] = "условие"
            workbook.save(project / relative)
            self.assertEqual(run_script(INDEX, project).returncode, 0)

            applied = run_script(INVENTORY, project, relative.as_posix(), "--apply")
            self.assertEqual(applied.returncode, 0, applied.stderr)
            inventory = json.loads((project / ".home-control" / "document_inventories.jsonl").read_text(encoding="utf-8"))
            self.assertEqual(inventory["expected_units"], ["Смета", "Скрытые данные"])
            self.assertEqual(inventory["features"]["sheets"][0]["formula_count"], 1)
            self.assertEqual(inventory["features"]["sheets"][0]["hidden_rows"], [2])
            self.assertIn("inspect hidden sheet", "\n".join(inventory["reading_requirements"]))
            document = json.loads((project / ".home-control" / "documents.json").read_text(encoding="utf-8"))["items"][0]
            summary = project / ".home-control" / "summaries" / "offer-v1.md"
            summary.write_text("# Конспект\n", encoding="utf-8")
            reading_run = {
                "reading_run_id": "RR-XLSX-1", "source_document_id": document["document_id"],
                "document_version": 1, "sha256": document["sha256"], "status": "complete",
                "coverage": {
                    "expected_units": inventory["expected_units"], "checked_units": inventory["expected_units"],
                    "checked_requirements": [], "gaps": [],
                },
                "summary_path": ".home-control/summaries/offer-v1.md",
            }
            write_jsonl(project / ".home-control" / "reading_runs.jsonl", reading_run)
            audited = run_script(AUDIT, project)
            self.assertEqual(audited.returncode, 1)
            self.assertIn("visual or structural reading requirements are unchecked", audited.stdout)
            reading_run["coverage"]["checked_requirements"] = inventory["reading_requirements"]
            write_jsonl(project / ".home-control" / "reading_runs.jsonl", reading_run)
            self.assertEqual(run_script(AUDIT, project).returncode, 0)

    def test_complete_reading_run_must_match_current_inventory(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            project = Path(temporary) / "project"
            self.assertEqual(run_script(INIT, project).returncode, 0)
            relative = Path("01_Обмеры_и_исходные_данные") / "source.txt"
            (project / relative).write_text("one\ntwo\n", encoding="utf-8")
            self.assertEqual(run_script(INDEX, project).returncode, 0)
            self.assertEqual(run_script(INVENTORY, project, relative.as_posix(), "--apply").returncode, 0)
            documents = json.loads((project / ".home-control" / "documents.json").read_text(encoding="utf-8"))
            document = documents["items"][0]
            summary = project / ".home-control" / "summaries" / "source-v1.md"
            summary.write_text("# Конспект\n", encoding="utf-8")
            reading_run = {
                "reading_run_id": "RR-MATCHED",
                "source_document_id": document["document_id"],
                "document_version": 1,
                "sha256": document["sha256"],
                "status": "complete",
                "coverage": {"expected_units": [1, 2], "checked_units": [1, 2], "gaps": []},
                "summary_path": ".home-control/summaries/source-v1.md",
            }
            write_jsonl(project / ".home-control" / "reading_runs.jsonl", reading_run)
            audited = run_script(AUDIT, project)
            self.assertEqual(audited.returncode, 0, audited.stdout + audited.stderr)

            reading_run["coverage"] = {"expected_units": [1], "checked_units": [1], "gaps": []}
            write_jsonl(project / ".home-control" / "reading_runs.jsonl", reading_run)
            audited = run_script(AUDIT, project)
            self.assertEqual(audited.returncode, 1, audited.stderr)
            self.assertIn("expected_units do not match the document inventory", audited.stdout)

    def test_generic_proposal_review_records_validates_and_builds_three_reports(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            project = Path(temporary) / "project"
            self.assertEqual(run_script(INIT, project).returncode, 0)
            baseline_relative = Path("02_Проекты_и_технические_решения") / "lighting-requirements.txt"
            proposal_relative = Path("03_Коммерческие_предложения") / "mixed-offer.txt"
            (project / baseline_relative).write_text(
                "В тестовой зоне установить два светильника с монтажом.\n",
                encoding="utf-8",
            )
            (project / proposal_relative).write_text(
                "Светильники 2 шт по 1000 руб.\nМонтаж включён.\n",
                encoding="utf-8",
            )
            self.assertEqual(run_script(INDEX, project).returncode, 0)
            self.assertEqual(run_script(INVENTORY, project, baseline_relative.as_posix(), "--apply").returncode, 0)
            self.assertEqual(run_script(INVENTORY, project, proposal_relative.as_posix(), "--apply").returncode, 0)
            control = project / ".home-control"
            documents = json.loads((control / "documents.json").read_text(encoding="utf-8"))["items"]
            baseline_document = next(
                value for value in documents if value["relative_path"] == baseline_relative.as_posix()
            )
            document = next(
                value for value in documents if value["relative_path"] == proposal_relative.as_posix()
            )
            inventories = [
                json.loads(line)
                for line in (control / "document_inventories.jsonl").read_text(encoding="utf-8").splitlines()
                if line.strip()
            ]
            baseline_inventory = next(
                value for value in inventories if value["source_document_id"] == baseline_document["document_id"]
            )
            inventory = next(value for value in inventories if value["source_document_id"] == document["document_id"])
            baseline_summary = control / "summaries" / "lighting-requirements-v1.md"
            baseline_summary.write_text("# Полный конспект требований\n", encoding="utf-8")
            summary = control / "summaries" / "mixed-offer-v1.md"
            summary.write_text("# Полный конспект КП\n", encoding="utf-8")
            fact = {
                "fact_id": "F-REQ-1",
                "statement": "Требуются два светильника с монтажом",
                "statement_kind": "source_fact",
                "evidence_origin": "approved_project_document",
                "verification_status": "verified",
                "source_document_id": baseline_document["document_id"],
                "document_version": 1,
                "sha256": baseline_document["sha256"],
                "locator": "строка 1, полное предложение",
                "recorded_at": "2026-08-19",
                "discipline_ids": ["electrical"],
                "package_ids": [],
                "site_ids": [],
                "zone_ids": [],
                "system_ids": [],
            }
            requirement = {
                "requirement_id": "AR-1",
                "statement": "Поставить и смонтировать два светильника",
                "scope": "тестовая зона",
                "baseline_status": "approved",
                "mandatory_parameters": {"quantity": 2},
                "source_fact_ids": ["F-REQ-1"],
                "verification_status": "verified",
                "decision_id": "D-BASE-1",
                "baseline_snapshot_id": "BL-1",
            }
            owner_fact = {
                "fact_id": "F-OWNER-2",
                "statement": "Отдельное требование владельца относится к другой зоне и не применяется к этому КП.",
                "statement_kind": "source_fact",
                "evidence_origin": "owner_confirmation",
                "verification_status": "verified",
                "locator": "решение владельца от 2026-08-19, пункт 2",
                "recorded_at": "2026-08-19",
                "discipline_ids": ["electrical"],
                "package_ids": [],
                "site_ids": [],
                "zone_ids": [],
                "system_ids": [],
            }
            unrelated_requirement = {
                "requirement_id": "AR-2",
                "statement": "Сохранить существующее освещение в другой зоне.",
                "scope": "другая зона",
                "baseline_status": "approved",
                "mandatory_parameters": {},
                "source_fact_ids": ["F-OWNER-2"],
                "verification_status": "verified",
                "decision_id": "D-BASE-1",
                "baseline_snapshot_id": "BL-1",
            }
            baseline_run = {
                "reading_run_id": "RR-BASE-1",
                "source_document_id": baseline_document["document_id"],
                "document_version": 1,
                "sha256": baseline_document["sha256"],
                "status": "complete",
                "coverage": {
                    "expected_units": baseline_inventory["expected_units"],
                    "checked_units": baseline_inventory["expected_units"],
                    "gaps": [],
                    "checked_requirements": baseline_inventory["reading_requirements"],
                },
                "summary_path": ".home-control/summaries/lighting-requirements-v1.md",
            }
            write_jsonl(control / "reading_runs.jsonl", baseline_run)
            baseline_package = {
                "schema_version": "1.0",
                "facts": [fact, owner_fact],
                "decisions": [{
                    "decision_id": "D-BASE-1",
                    "decision_type": "baseline_acceptance",
                    "status": "approved",
                    "approved_by": "owner",
                    "approved_at": "2026-08-19T12:00:00+03:00",
                    "decision": "Принять версию 1 документа требований как базу анализа КП.",
                    "source_fact_ids": ["F-REQ-1", "F-OWNER-2"],
                }],
                "approved_requirements": [requirement, unrelated_requirement],
                "baseline_snapshots": [{
                    "baseline_snapshot_id": "BL-1",
                    "baseline_version": 1,
                    "scope": "тестовая зона, освещение",
                    "accepted_at": "2026-08-19T12:00:00+03:00",
                    "owner_decision_id": "D-BASE-1",
                    "supersedes_baseline_snapshot_id": "",
                    "requirement_ids": ["AR-1", "AR-2"],
                    "owner_requirement_ids": ["AR-2"],
                    "document_versions": [{
                        "document_id": baseline_document["document_id"],
                        "document_version": 1,
                        "sha256": baseline_document["sha256"],
                        "project_role": "требования к освещению",
                        "applicability_scope": "тестовая зона",
                        "technical_approval_status": "unknown",
                        "official_approval_status": "unknown",
                        "requirement_ids": ["AR-1"],
                    }],
                    "conflict_resolutions": [],
                }],
            }
            baseline_package_path = project / "baseline-package.json"
            baseline_package_path.write_text(
                json.dumps(baseline_package, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            proposal_as_baseline = json.loads(json.dumps(baseline_package))
            proposal_as_baseline["facts"][0].update({
                "source_document_id": document["document_id"],
                "sha256": document["sha256"],
                "locator": "строка 1 КП",
            })
            proposal_as_baseline["baseline_snapshots"][0]["document_versions"][0].update({
                "document_id": document["document_id"],
                "sha256": document["sha256"],
                "project_role": "коммерческое предложение",
            })
            proposal_baseline_path = project / "proposal-as-baseline.json"
            proposal_baseline_path.write_text(
                json.dumps(proposal_as_baseline, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            rejected_proposal_baseline = run_script(RECORD_BASELINE, project, proposal_baseline_path)
            self.assertEqual(rejected_proposal_baseline.returncode, 2)
            self.assertIn("quote source cannot be part of the baseline", rejected_proposal_baseline.stderr)

            non_owner_baseline = json.loads(json.dumps(baseline_package))
            non_owner_baseline["decisions"][0]["approved_by"] = "technical_reviewer"
            non_owner_path = project / "non-owner-baseline.json"
            non_owner_path.write_text(
                json.dumps(non_owner_baseline, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            rejected_non_owner = run_script(RECORD_BASELINE, project, non_owner_path)
            self.assertEqual(rejected_non_owner.returncode, 2)
            self.assertIn("explicitly approved by the owner", rejected_non_owner.stderr)

            baseline_preview = run_script(RECORD_BASELINE, project, baseline_package_path)
            self.assertEqual(baseline_preview.returncode, 0, baseline_preview.stderr)
            baseline_applied = run_script(RECORD_BASELINE, project, baseline_package_path, "--apply")
            self.assertEqual(baseline_applied.returncode, 0, baseline_applied.stderr)
            regulatory_path = project / "proposal-regulatory-package.json"
            regulatory_path.write_text(
                json.dumps(
                    regulatory_test_package(document["document_id"], "F-REQ-1"),
                    ensure_ascii=False,
                    indent=2,
                ),
                encoding="utf-8",
            )
            regulatory_applied = run_script(RECORD_REGULATORY, project, regulatory_path, "--apply")
            self.assertEqual(regulatory_applied.returncode, 0, regulatory_applied.stderr)
            quote_fact = {
                "fact_id": "F-QUOTE-1",
                "statement": "КП предлагает два светильника по 1000 рублей с монтажом",
                "statement_kind": "source_fact",
                "evidence_origin": "contractor_proposal",
                "verification_status": "verified",
                "source_document_id": document["document_id"],
                "document_version": 1,
                "sha256": document["sha256"],
                "locator": "строки 1-2",
                "recorded_at": "2026-08-19",
                "discipline_ids": ["electrical", "equipment_supply"],
                "package_ids": ["PKG-1"],
                "site_ids": [],
                "zone_ids": [],
                "system_ids": [],
            }
            package = {
                "schema_version": "1.0",
                "facts": [quote_fact],
                "reading_runs": [{
                    "reading_run_id": "RR-OFFER-1",
                    "source_document_id": document["document_id"],
                    "document_version": 1,
                    "sha256": document["sha256"],
                    "status": "complete",
                    "coverage": {
                        "expected_units": [1, 2],
                        "checked_units": [1, 2],
                        "checked_requirements": inventory["reading_requirements"],
                        "gaps": [],
                    },
                    "summary_path": ".home-control/summaries/mixed-offer-v1.md",
                }],
                "project_packages": [{
                    "package_id": "PKG-1",
                    "name": "Поставка и монтаж освещения тестовой зоны",
                    "goal": "Получить два установленных и проверенных светильника",
                    "status": "in_analysis",
                    "disciplines": ["electrical", "equipment_supply"],
                    "source_document_versions": [{
                        "document_id": document["document_id"],
                        "document_version": 1,
                        "sha256": document["sha256"],
                    }],
                    "fact_ids": ["F-QUOTE-1"],
                    "requirement_ids": ["AR-1"],
                    "information_gap_ids": [],
                    "site_ids": [],
                    "zone_ids": [],
                    "system_ids": [],
                }],
                "fact_extraction_runs": [{
                    "extraction_run_id": "FER-OFFER-1",
                    "source_document_id": document["document_id"],
                    "document_version": 1,
                    "sha256": document["sha256"],
                    "reading_run_id": "RR-OFFER-1",
                    "expected_sections": ["предмет и объём", "цена и условия"],
                    "checked_sections": ["предмет и объём", "цена и условия"],
                    "coverage_gaps": [],
                    "fact_ids": ["F-QUOTE-1"],
                    "requirement_ids": [],
                    "information_gap_ids": [],
                    "conflict_fact_ids": [],
                    "status": "complete",
                }],
                "information_gaps": [],
                "shared_resources": [],
                "resource_demands": [],
                "package_interfaces": [],
                "coordination_issues": [],
                "coordination_runs": [],
                "contractors": [
                    {"contractor_id": "CTR-1", "name": "Подрядчик из проверяемого КП"},
                    {"contractor_id": "CTR-2", "name": "Отдельный сопоставимый кандидат"},
                ],
                "quotes": [{
                    "quote_id": "Q-1", "contractor_id": "CTR-1",
                    "source_document_id": document["document_id"], "document_version": 1,
                    "sha256": document["sha256"], "currency": "RUB", "status": "under_review",
                }],
                "quote_items": [{
                    "quote_item_id": "QI-1", "quote_id": "Q-1",
                    "raw_text": "Светильники 2 шт по 1000 руб., монтаж включён", "locator": "строки 1-2",
                    "quantity": 2, "unit": "шт", "unit_price": 1000, "amount": 2000,
                    "approved_requirement_ids": ["AR-1"], "target_entity_ids": [],
                    "proposal_match_status": "exact", "verifiability": "verifiable",
                }],
                "findings": [
                    {
                        "finding_id": "FN-1", "statement": "В КП не указан срок гарантии",
                        "finding_type": "scope_gap", "severity": "medium", "source_ids": ["QI-1"],
                    },
                    {
                        "finding_id": "FN-2", "statement": "Арифметика строки подтверждена",
                        "finding_type": "strength", "severity": "positive", "source_ids": ["QI-1"],
                    },
                ],
                "alternatives": [
                    {
                        "alternative_id": alternative_id,
                        "description": description,
                        "baseline_requirement_ids": ["AR-1"],
                        "checked_at": "2026-08-19",
                        "source_urls": [f"https://example.test/{alternative_id.lower()}"],
                    }
                    for alternative_id, description in (
                        ("ALT-OPT", "Уточнить и улучшить состав решения подрядчика"),
                        ("ALT-SAME", "Сопоставить решение того же класса"),
                        ("ALT-DIFF", "Проверить иной технический принцип"),
                        ("ALT-DEFER", "Сравнить отсрочку и отказ от вмешательства"),
                    )
                ],
                "proposal_reviews": [{
                    "proposal_review_id": "PR-1", "source_document_id": document["document_id"],
                    "document_version": 1, "sha256": document["sha256"], "quote_id": "Q-1",
                    "status": "ready_for_owner", "disciplines": ["electrical", "equipment_supply"],
                    "inventory_id": inventory["inventory_id"], "reading_run_ids": ["RR-OFFER-1"],
                    "fact_extraction_run_ids": ["FER-OFFER-1"],
                    "project_package_ids": ["PKG-1"],
                    "information_gap_ids": [],
                    "coordination_issue_ids": [],
                    "compliance_assessment_ids": ["RCA-1"],
                    "baseline_assessment_mode": "accepted_baseline",
                    "baseline_snapshot_id": "BL-1",
                    "baseline_applicability_scope": "тестовая зона, поставка и монтаж освещения",
                    "baseline_requirement_ids": ["AR-1"],
                    "requirement_matches": [{"requirement_id": "AR-1", "status": "exact", "quote_item_ids": ["QI-1"]}],
                    "reference_comparisons": [],
                    "baseline_limitations": [],
                    "unmatched_quote_item_ids": [],
                    "technical_checks": [{
                        "check_id": "TC-1", "category": "scope", "criterion": "Поставка и монтаж включены",
                        "status": "satisfied", "source_ids": ["QI-1", "AR-1"],
                    }],
                    "calculations": [{
                        "calculation_id": "CALC-1", "formula": "quantity * unit_price",
                        "inputs": [
                            {"name": "quantity", "value": 2, "unit": "шт", "source_ids": ["QI-1"]},
                            {"name": "unit_price", "value": 1000, "unit": "RUB/шт", "source_ids": ["QI-1"]},
                        ],
                        "result": 2000, "unit": "RUB", "status": "verified",
                    }],
                    "search_runs": [{
                        "search_run_id": "SR-1", "status": "complete",
                        "queries": ["монтаж двух светильников подрядчик тестовый регион"],
                        "checked_at": "2026-08-19", "region": "тестовый регион",
                        "source_urls": ["https://example.test/contractor"],
                        "candidate_contractor_ids": ["CTR-2"],
                        "candidate_supplier_ids": [],
                        "candidate_assessments": [{
                            "counterparty_id": "CTR-2",
                            "counterparty_kind": "contractor",
                            "comparability_status": "requires_quote",
                            "basis": "Кандидат выполняет тот же вид работ в тестовом регионе",
                            "missing_information": ["нужно получить цену по единому заданию"],
                            "source_urls": ["https://example.test/contractor"],
                        }],
                        "privacy_review": {"unnecessary_private_data_removed": True},
                    }],
                    "finding_ids": ["FN-1", "FN-2"],
                    "alternative_ids": ["ALT-OPT", "ALT-SAME", "ALT-DIFF", "ALT-DEFER"],
                    "essential_blockers": [],
                    "contractor_questions": ["Подтвердите срок гарантии"],
                    **complete_proposal_contract(
                        ["electrical", "equipment_supply"],
                        ["QI-1", "AR-1"],
                        ["ALT-OPT", "ALT-SAME", "ALT-DIFF", "ALT-DEFER"],
                        ["QI-1"],
                        ["AR-1"],
                        ["FN-1"],
                        ["RCA-1"],
                    ),
                }],
            }
            reference_project = Path(temporary) / "reference-project"
            self.assertEqual(run_script(INIT, reference_project).returncode, 0)
            (reference_project / baseline_relative).write_text(
                "В тестовой зоне установить два светильника с монтажом.\n",
                encoding="utf-8",
            )
            (reference_project / proposal_relative).write_text(
                "Светильники 2 шт по 1000 руб.\nМонтаж включён.\n",
                encoding="utf-8",
            )
            self.assertEqual(run_script(INDEX, reference_project).returncode, 0)
            self.assertEqual(
                run_script(INVENTORY, reference_project, baseline_relative.as_posix(), "--apply").returncode,
                0,
            )
            self.assertEqual(
                run_script(INVENTORY, reference_project, proposal_relative.as_posix(), "--apply").returncode,
                0,
            )
            reference_control = reference_project / ".home-control"
            (reference_control / "summaries" / "lighting-requirements-v1.md").write_text(
                "# Полный справочный конспект требований\n",
                encoding="utf-8",
            )
            (reference_control / "summaries" / "mixed-offer-v1.md").write_text(
                "# Полный конспект КП\n",
                encoding="utf-8",
            )
            write_jsonl(reference_control / "reading_runs.jsonl", baseline_run)
            reference_norm_fact = {
                "fact_id": "F-NORM-REF",
                "statement": "КП описывает монтаж освещения с проверяемым результатом",
                "statement_kind": "source_fact",
                "evidence_origin": "contractor_proposal",
                "verification_status": "verified",
                "source_document_id": document["document_id"],
                "document_version": 1,
                "sha256": document["sha256"],
                "locator": "строки 1-2",
                "recorded_at": "2026-08-19",
                "discipline_ids": ["electrical"],
                "package_ids": [],
                "site_ids": [],
                "zone_ids": [],
                "system_ids": [],
            }
            write_jsonl(reference_control / "facts.jsonl", reference_norm_fact)
            reference_regulatory_path = reference_project / "proposal-regulatory-package.json"
            reference_regulatory_path.write_text(
                json.dumps(
                    regulatory_test_package(document["document_id"], "F-NORM-REF"),
                    ensure_ascii=False,
                    indent=2,
                ),
                encoding="utf-8",
            )
            reference_regulatory = run_script(
                RECORD_REGULATORY,
                reference_project,
                reference_regulatory_path,
                "--apply",
            )
            self.assertEqual(reference_regulatory.returncode, 0, reference_regulatory.stderr)

            reference_package = json.loads(json.dumps(package))

            def remove_baseline_identifier(value: object) -> None:
                if isinstance(value, dict):
                    for nested in value.values():
                        remove_baseline_identifier(nested)
                elif isinstance(value, list):
                    value[:] = [nested for nested in value if nested != "AR-1"]
                    for nested in value:
                        remove_baseline_identifier(nested)

            remove_baseline_identifier(reference_package)
            reference_review = reference_package["proposal_reviews"][0]
            reference_review.update({
                "proposal_review_id": "PR-REF-1",
                "baseline_assessment_mode": "reference_only",
                "baseline_snapshot_id": "",
                "baseline_applicability_scope": "",
                "baseline_requirement_ids": [],
                "requirement_matches": [],
                "unmatched_quote_item_ids": ["QI-1"],
                "reference_comparisons": [{
                    "document_id": baseline_document["document_id"],
                    "document_version": 1,
                    "sha256": baseline_document["sha256"],
                    "project_role": "справочные требования к освещению",
                    "applicability_scope": "тестовая зона",
                    "statement": "КП справочно совпадает с требованием о двух светильниках и монтаже.",
                    "locator": "строка 1, полное предложение",
                    "limitations": "Документ не принят владельцем как применяемая базовая линия.",
                    "status": "exact",
                    "quote_item_ids": ["QI-1"],
                }],
                "baseline_limitations": [
                    "Соответствие принятой базе не оценено до отдельного решения владельца."
                ],
            })
            reference_review["foreman_assessment"]["decision_readiness"] = "ready_for_negotiation"
            for assessment in reference_review["technical_alternative_assessments"]:
                assessment["project_fit"] = "Сопоставлено только справочно; принятая база отсутствует."
            reference_package_path = reference_project / "reference-only-package.json"
            reference_package_path.write_text(
                json.dumps(reference_package, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            reference_preview = run_script(RECORD_PROPOSAL, reference_project, reference_package_path)
            self.assertEqual(reference_preview.returncode, 0, reference_preview.stderr)
            invalid_reference = json.loads(json.dumps(reference_package))
            invalid_reference["proposal_reviews"][0]["foreman_assessment"]["decision_readiness"] = "ready_for_contract"
            invalid_reference_path = reference_project / "invalid-reference-only-package.json"
            invalid_reference_path.write_text(
                json.dumps(invalid_reference, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            rejected_reference = run_script(RECORD_PROPOSAL, reference_project, invalid_reference_path)
            self.assertEqual(rejected_reference.returncode, 2)
            self.assertIn("ready_for_contract requires an accepted baseline", rejected_reference.stderr)
            reference_applied = run_script(RECORD_PROPOSAL, reference_project, reference_package_path, "--apply")
            self.assertEqual(reference_applied.returncode, 0, reference_applied.stderr)
            self.assertEqual(run_script(AUDIT, reference_project).returncode, 0)
            self.assertEqual(run_script(BUILD_DOSSIER, reference_project, "PR-REF-1", "--apply").returncode, 0)
            reference_dossier = (
                reference_control / "reports" / "proposals" / "PR-REF-1" / "full-dossier.md"
            ).read_text(encoding="utf-8")
            self.assertIn("### Справочные сопоставления", reference_dossier)
            self.assertIn("Не оценивалось: применяемая базовая линия не принята", reference_dossier)

            package_path = project / "proposal-package.json"
            package_path.write_text(json.dumps(package, ensure_ascii=False, indent=2), encoding="utf-8")

            incomplete_extraction = json.loads(json.dumps(package))
            incomplete_extraction["fact_extraction_runs"][0]["checked_sections"] = ["предмет и объём"]
            incomplete_extraction_path = project / "incomplete-extraction-package.json"
            incomplete_extraction_path.write_text(
                json.dumps(incomplete_extraction, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            rejected_extraction = run_script(RECORD_PROPOSAL, project, incomplete_extraction_path)
            self.assertEqual(rejected_extraction.returncode, 2)
            self.assertIn("semantic", rejected_extraction.stderr.lower())

            invalid_existing_run = json.loads(json.dumps(package["reading_runs"][0]))
            invalid_existing_run["coverage"]["checked_units"] = [1]
            write_jsonl(control / "reading_runs.jsonl", invalid_existing_run)
            references_bad_run = json.loads(json.dumps(package))
            references_bad_run["reading_runs"] = []
            bad_run_path = project / "bad-run-package.json"
            bad_run_path.write_text(json.dumps(references_bad_run, ensure_ascii=False), encoding="utf-8")
            rejected_bad_run = run_script(RECORD_PROPOSAL, project, bad_run_path)
            self.assertEqual(rejected_bad_run.returncode, 2)
            self.assertIn("coverage or blockers", rejected_bad_run.stderr)
            write_jsonl(control / "reading_runs.jsonl", baseline_run)

            invalid = json.loads(json.dumps(package))
            invalid["proposal_reviews"][0]["requirement_matches"] = []
            invalid_path = project / "invalid-package.json"
            invalid_path.write_text(json.dumps(invalid, ensure_ascii=False), encoding="utf-8")
            rejected = run_script(RECORD_PROPOSAL, project, invalid_path)
            self.assertEqual(rejected.returncode, 2)
            self.assertEqual((control / "proposal_reviews.jsonl").read_text(encoding="utf-8"), "")

            missing_compliance = json.loads(json.dumps(package))
            missing_compliance["proposal_reviews"][0]["compliance_assessment_ids"] = []
            missing_compliance_path = project / "missing-compliance-assessment.json"
            missing_compliance_path.write_text(
                json.dumps(missing_compliance, ensure_ascii=False), encoding="utf-8"
            )
            rejected_compliance = run_script(RECORD_PROPOSAL, project, missing_compliance_path)
            self.assertEqual(rejected_compliance.returncode, 2)
            self.assertIn("normative check needs a ComplianceAssessment", rejected_compliance.stderr)

            missing_alternative = json.loads(json.dumps(package))
            missing_alternative["proposal_reviews"][0]["technical_alternative_assessments"] = [
                value
                for value in missing_alternative["proposal_reviews"][0]["technical_alternative_assessments"]
                if value["track_id"] != "different_technical_principle"
            ]
            missing_alternative_path = project / "missing-technical-alternative.json"
            missing_alternative_path.write_text(
                json.dumps(missing_alternative, ensure_ascii=False), encoding="utf-8"
            )
            rejected_alternative = run_script(RECORD_PROPOSAL, project, missing_alternative_path)
            self.assertEqual(rejected_alternative.returncode, 2)
            self.assertIn("technical_alternative_assessments", rejected_alternative.stderr)

            specialist_required = json.loads(json.dumps(package))
            specialist_required["proposal_reviews"][0]["mandatory_checks"][0].update({
                "status": "requires_specialist",
                "result": "Полнота чтения требует проверки вложенного чертежа специалистом",
                "rationale": "Не подтверждена читаемость условных обозначений на одном листе",
                "required_inputs": ["заключение профильного специалиста"],
            })
            specialist_path = project / "specialist-required-package.json"
            specialist_path.write_text(
                json.dumps(specialist_required, ensure_ascii=False), encoding="utf-8"
            )
            rejected_specialist = run_script(RECORD_PROPOSAL, project, specialist_path)
            self.assertEqual(rejected_specialist.returncode, 2)
            self.assertIn("blocked or specialist-required", rejected_specialist.stderr)

            invalid_manifest = json.loads(json.dumps(package))
            invalid_manifest["proposal_reviews"][0]["completion_manifest"]["mandatory_check_ids"][0] = 1
            invalid_manifest_path = project / "invalid-manifest-package.json"
            invalid_manifest_path.write_text(
                json.dumps(invalid_manifest, ensure_ascii=False), encoding="utf-8"
            )
            rejected_manifest = run_script(RECORD_PROPOSAL, project, invalid_manifest_path)
            self.assertEqual(rejected_manifest.returncode, 2)
            self.assertIn("completion_manifest.mandatory_check_ids", rejected_manifest.stderr)

            placeholder = json.loads(json.dumps(package))
            placeholder["proposal_reviews"][0]["mandatory_checks"][0].pop("observations")
            placeholder_path = project / "placeholder-review-package.json"
            placeholder_path.write_text(json.dumps(placeholder, ensure_ascii=False), encoding="utf-8")
            rejected_placeholder = run_script(RECORD_PROPOSAL, project, placeholder_path)
            self.assertEqual(rejected_placeholder.returncode, 2)
            self.assertIn("requires evidence observations", rejected_placeholder.stderr)

            waived_universal = json.loads(json.dumps(package))
            waived_universal["proposal_reviews"][0]["mandatory_checks"][0].update({
                "status": "not_applicable",
                "rationale": "тестовая попытка исключить универсальную проверку",
                "applicability_evidence": "КП существует, поэтому исключение заведомо неверно",
            })
            waived_universal_path = project / "waived-universal-package.json"
            waived_universal_path.write_text(json.dumps(waived_universal, ensure_ascii=False), encoding="utf-8")
            rejected_waiver = run_script(RECORD_PROPOSAL, project, waived_universal_path)
            self.assertEqual(rejected_waiver.returncode, 2)
            self.assertIn("cannot be not_applicable", rejected_waiver.stderr)

            self_candidate = json.loads(json.dumps(package))
            self_candidate["proposal_reviews"][0]["search_runs"][0]["candidate_contractor_ids"] = ["CTR-1"]
            self_candidate["proposal_reviews"][0]["search_runs"][0]["candidate_assessments"][0]["counterparty_id"] = "CTR-1"
            self_candidate_path = project / "self-candidate-package.json"
            self_candidate_path.write_text(json.dumps(self_candidate, ensure_ascii=False), encoding="utf-8")
            rejected_self_candidate = run_script(RECORD_PROPOSAL, project, self_candidate_path)
            self.assertEqual(rejected_self_candidate.returncode, 2)
            self.assertIn("distinct comparable contractor", rejected_self_candidate.stderr)

            uncovered_scope = json.loads(json.dumps(package))
            uncovered_scope["proposal_reviews"][0]["scope_boundary_matrix"][0]["quote_item_ids"] = []
            uncovered_scope_path = project / "uncovered-scope-package.json"
            uncovered_scope_path.write_text(json.dumps(uncovered_scope, ensure_ascii=False), encoding="utf-8")
            rejected_scope = run_script(RECORD_PROPOSAL, project, uncovered_scope_path)
            self.assertEqual(rejected_scope.returncode, 2)
            self.assertIn("scope boundary", rejected_scope.stderr)

            unresolved_cost = json.loads(json.dumps(package))
            unresolved_cost["proposal_reviews"][0]["cost_exposure"].update({
                "status": "partial",
                "estimated_total_high": None,
                "unknown_exposures": [{
                    "description": "стоимость восстановления отделки",
                    "reason": "нет обследования",
                    "blocking": True,
                    "source_ids": ["QI-1"],
                }],
            })
            unresolved_cost_path = project / "unresolved-cost-package.json"
            unresolved_cost_path.write_text(json.dumps(unresolved_cost, ensure_ascii=False), encoding="utf-8")
            rejected_cost = run_script(RECORD_PROPOSAL, project, unresolved_cost_path)
            self.assertEqual(rejected_cost.returncode, 2)
            self.assertIn("ready_for_contract has unresolved cost exposure", rejected_cost.stderr)

            supplier_package = json.loads(json.dumps(package))
            supplier_package["contractors"] = []
            supplier_package["suppliers"] = [
                {"supplier_id": "SUP-1", "name": "Поставщик из проверяемого КП"},
                {"supplier_id": "SUP-2", "name": "Отдельный поставщик-кандидат"},
            ]
            supplier_package["quotes"][0].pop("contractor_id")
            supplier_package["quotes"][0]["supplier_id"] = "SUP-1"
            supplier_search = supplier_package["proposal_reviews"][0]["search_runs"][0]
            supplier_search["candidate_contractor_ids"] = []
            supplier_search["candidate_supplier_ids"] = ["SUP-2"]
            supplier_search["candidate_assessments"][0].update({
                "counterparty_id": "SUP-2",
                "counterparty_kind": "supplier",
                "basis": "Кандидат поставляет сопоставимый предмет в тестовом регионе",
            })
            supplier_package["proposal_reviews"][0]["scope_boundary_matrix"][0]["responsibilities"] = {
                role: "SUP-1" for role in PROPOSAL_CONTRACT["scope_responsibility_roles"]
            }
            supplier_path = project / "supplier-proposal-package.json"
            supplier_path.write_text(json.dumps(supplier_package, ensure_ascii=False), encoding="utf-8")
            supplier_preview = run_script(RECORD_PROPOSAL, project, supplier_path)
            self.assertEqual(supplier_preview.returncode, 0, supplier_preview.stderr)

            preview = run_script(RECORD_PROPOSAL, project, package_path)
            self.assertEqual(preview.returncode, 0, preview.stderr)
            self.assertEqual(json.loads(preview.stdout)["append"]["proposal_reviews"], 1)
            applied = run_script(RECORD_PROPOSAL, project, package_path, "--apply")
            self.assertEqual(applied.returncode, 0, applied.stderr)
            self.assertEqual(run_script(AUDIT, project).returncode, 0)

            dossier_preview = run_script(BUILD_DOSSIER, project, "PR-1")
            self.assertEqual(dossier_preview.returncode, 0, dossier_preview.stderr)
            target = control / "reports" / "proposals" / "PR-1"
            self.assertFalse(target.exists())
            created = run_script(BUILD_DOSSIER, project, "PR-1", "--apply")
            self.assertEqual(created.returncode, 0, created.stderr)
            self.assertEqual(
                {path.name for path in target.iterdir()},
                {"owner-card.md", "contractor-request.md", "full-dossier.md"},
            )
            dossier_text = (target / "full-dossier.md").read_text(encoding="utf-8")
            owner_text = (target / "owner-card.md").read_text(encoding="utf-8")
            owner_summary = owner_text.split("\n\nПолное обоснование:", 1)[0].split("\n\n", 1)[1]
            self.assertIn(owner_summary, dossier_text)
            self.assertEqual(owner_text.count("## Решение владельца — кратко"), 1)
            self.assertIn("Что нужно решить", owner_text)
            self.assertIn("`ALT-OPT`", owner_text)
            self.assertIn("electrical, equipment_supply", dossier_text)
            self.assertIn("## Альтернативные технические решения", dossier_text)
            self.assertIn("different_technical_principle", dossier_text)
            self.assertIn("## Дополнительный анализ модели", dossier_text)
            self.assertIn("## Прорабский вывод", dossier_text)
            self.assertIn("## Границы объёма и ответственности", dossier_text)
            self.assertIn("## Полная денежная экспозиция", dossier_text)
            self.assertIn("## Нормативное соответствие", dossier_text)
            self.assertIn("RCA-1", dossier_text)
            refused = run_script(BUILD_DOSSIER, project, "PR-1", "--apply")
            self.assertEqual(refused.returncode, 2)
            escaped = run_script(BUILD_DOSSIER, project, "../escape", "--apply")
            self.assertEqual(escaped.returncode, 2)
            self.assertFalse((control / "reports" / "escape").exists())

            baseline_v2 = json.loads(json.dumps(baseline_package))
            replacements = {
                "BL-1": "BL-2",
                "D-BASE-1": "D-BASE-2",
                "F-REQ-1": "F-REQ-2",
                "F-OWNER-2": "F-OWNER-3",
                "AR-1": "AR-3",
                "AR-2": "AR-4",
            }

            def replace_identifiers(value: object) -> object:
                if isinstance(value, dict):
                    return {key: replace_identifiers(nested) for key, nested in value.items()}
                if isinstance(value, list):
                    return [replace_identifiers(nested) for nested in value]
                if isinstance(value, str):
                    return replacements.get(value, value)
                return value

            baseline_v2 = replace_identifiers(baseline_v2)
            baseline_v2["decisions"][0].update({
                "approved_at": "2026-08-20T12:00:00+03:00",
                "decision": "Принять вторую версию базовой линии без изменения исходного документа.",
            })
            baseline_v2["baseline_snapshots"][0].update({
                "baseline_version": 2,
                "accepted_at": "2026-08-20T12:00:00+03:00",
                "supersedes_baseline_snapshot_id": "BL-1",
            })
            baseline_v2_path = project / "baseline-v2-package.json"
            baseline_v2_path.write_text(
                json.dumps(baseline_v2, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            baseline_v2_preview = run_script(RECORD_BASELINE, project, baseline_v2_path)
            self.assertEqual(baseline_v2_preview.returncode, 0, baseline_v2_preview.stderr)
            baseline_v2_applied = run_script(RECORD_BASELINE, project, baseline_v2_path, "--apply")
            self.assertEqual(baseline_v2_applied.returncode, 0, baseline_v2_applied.stderr)
            self.assertEqual(run_script(AUDIT, project).returncode, 0)
            self.assertIn("`BL-1`", (target / "full-dossier.md").read_text(encoding="utf-8"))


    def prepare_management_project(self, project: Path) -> None:
        self.assertEqual(run_script(INIT, project).returncode, 0)
        control = project / ".home-control"
        write_jsonl(
            control / "facts.jsonl",
            {
                "fact_id": "F-MGT",
                "statement": "Подтверждённое основание управленческого плана",
                "statement_kind": "source_fact",
                "evidence_origin": "owner_confirmation",
                "verification_status": "verified",
            },
        )
        write_jsonl(
            control / "decisions.jsonl",
            {
                "decision_id": "D-MGT",
                "decision_type": "management_baseline_acceptance",
                "status": "approved",
                "decision": "Принять стоимость, срок и изменение",
            },
        )
        write_jsonl(
            control / "baseline_snapshots.jsonl",
            {"baseline_snapshot_id": "BL-MGT", "baseline_version": 1},
        )
        append_csv_row(
            control / "work_items.csv",
            {"work_item_id": "W-1", "title": "Подготовка", "status": "not_started"},
        )
        append_csv_row(
            control / "work_items.csv",
            {"work_item_id": "W-2", "title": "Основные работы", "status": "not_started"},
        )
        append_csv_row(
            control / "changes.csv",
            {
                "change_id": "CH-1",
                "date": "2026-01-02",
                "work_item_id": "W-2",
                "description": "Утверждённое изменение",
                "status": "approved",
                "decision_id": "D-MGT",
            },
        )

    def test_linked_management_cycle_calculates_and_records_plan_fact_forecast(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            project = Path(temporary) / "project"
            self.prepare_management_project(project)
            control = project / ".home-control"
            documents = json.loads((control / "documents.json").read_text(encoding="utf-8"))
            documents["items"].append({"document_id": "DOC-PAY", "status": "active", "versions": []})
            (control / "documents.json").write_text(
                json.dumps(documents, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )
            append_csv_row(
                control / "costs.csv",
                {
                    "cost_id": "C-IN-SCOPE",
                    "date": "2026-01-06",
                    "work_item_id": "W-1",
                    "amount": 40,
                    "currency": "RUB",
                    "status": "confirmed_paid",
                    "evidence_document_id": "DOC-PAY",
                    "evidence_locator": "строка 1",
                },
            )
            append_csv_row(
                control / "costs.csv",
                {
                    "cost_id": "C-FOREIGN-SCOPE",
                    "date": "2026-01-06",
                    "work_item_id": "W-FOREIGN",
                    "amount": 999,
                    "currency": "RUB",
                    "status": "confirmed_paid",
                    "evidence_document_id": "DOC-PAY",
                    "evidence_locator": "строка 2",
                },
            )
            package_path = project / "management-package.json"
            package_path.write_text(
                json.dumps(management_package(), ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            before = (control / "cost_plans.jsonl").read_bytes()
            preview = run_script(MANAGEMENT, project, package_path)
            self.assertEqual(preview.returncode, 0, preview.stderr)
            preview_value = json.loads(preview.stdout)
            self.assertEqual(preview_value["calculated_cost_totals"]["CPL-1"], 300.0)
            self.assertEqual(preview_value["calculated_schedule_finishes"]["SPL-1"], "2026-01-14")
            self.assertEqual((control / "cost_plans.jsonl").read_bytes(), before)

            applied = run_script(MANAGEMENT, project, package_path, "--apply")
            self.assertEqual(applied.returncode, 0, applied.stderr)
            cost_plan = read_jsonl(control / "cost_plans.jsonl")[0]
            schedule_plan = read_jsonl(control / "schedule_plans.jsonl")[0]
            snapshot = read_jsonl(control / "control_snapshots.jsonl")[0]
            self.assertEqual(cost_plan["total_amount"], 300.0)
            self.assertEqual(schedule_plan["calculated_finish"], "2026-01-14")
            self.assertTrue(schedule_plan["activities"][0]["is_critical"])
            self.assertEqual(snapshot["metrics"]["current_budget"], 350.0)
            self.assertEqual(snapshot["metrics"]["confirmed_actual_cost"], 40.0)
            self.assertEqual(snapshot["metrics"]["forecast_at_completion"], 290.0)
            self.assertEqual(snapshot["metrics"]["cost_variance_at_completion"], 60.0)
            self.assertEqual(snapshot["metrics"]["schedule_variance_calendar_days"], 2)

            dashboard = run_script(DASHBOARD, project)
            self.assertEqual(dashboard.returncode, 0, dashboard.stderr)
            report = (control / "reports" / "project-status.md").read_text(encoding="utf-8")
            self.assertIn("Управленческая база и прогноз", report)
            self.assertIn("Текущий бюджет: 350.00 RUB", report)
            self.assertIn("Прогноз итоговой стоимости: 290.00 RUB", report)
            duplicate = run_script(MANAGEMENT, project, package_path, "--apply")
            self.assertEqual(duplicate.returncode, 2)
            self.assertIn("append-only", duplicate.stderr)

    def test_management_cycle_rejects_dependency_cycle_without_writing(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            project = Path(temporary) / "project"
            self.prepare_management_project(project)
            package = management_package()
            package["schedule_plans"][0]["activities"][0]["predecessors"] = [
                {"activity_id": "ACT-2", "relationship": "FS", "lag_workdays": 0}
            ]
            package_path = project / "cyclic-management-package.json"
            package_path.write_text(json.dumps(package, ensure_ascii=False), encoding="utf-8")
            before = file_snapshot(project)
            rejected = run_script(MANAGEMENT, project, package_path, "--apply")
            self.assertEqual(rejected.returncode, 2)
            self.assertIn("dependency cycle", rejected.stderr)
            self.assertEqual(file_snapshot(project), before)

    def test_management_cycle_rejects_impossible_schedule_constraint(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            project = Path(temporary) / "project"
            self.prepare_management_project(project)
            package = management_package()
            package["schedule_plans"][0]["activities"][1]["not_after"] = "2026-01-13"
            package_path = project / "impossible-schedule-package.json"
            package_path.write_text(json.dumps(package, ensure_ascii=False), encoding="utf-8")
            rejected = run_script(MANAGEMENT, project, package_path)
            self.assertEqual(rejected.returncode, 2)
            self.assertIn("violates declared date constraints", rejected.stderr)

    def test_audit_detects_invalid_management_links(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            project = Path(temporary) / "project"
            self.assertEqual(run_script(INIT, project).returncode, 0)
            write_jsonl(
                project / ".home-control" / "cost_plans.jsonl",
                {
                    "cost_plan_id": "CPL-BROKEN",
                    "plan_series_id": "COST-BROKEN",
                    "revision": 1,
                    "status": "ready_for_baseline",
                    "baseline_snapshot_id": "BL-UNKNOWN",
                    "valuation_date": "2026-01-01",
                    "currency": "RUB",
                    "items": [],
                },
            )
            audited = run_script(AUDIT, project)
            self.assertEqual(audited.returncode, 1, audited.stderr)
            self.assertIn("management-cycle", audited.stdout)
            self.assertIn("known BaselineSnapshot", audited.stdout)


if __name__ == "__main__":
    unittest.main()
