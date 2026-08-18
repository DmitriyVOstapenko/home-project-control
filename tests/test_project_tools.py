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
DASHBOARD = REPO_ROOT / "plugins" / "home-project-control" / "skills" / "track-progress-and-cost" / "scripts" / "build_dashboard.py"
STRUCTURE = json.loads(
    (REPO_ROOT / "plugins" / "home-project-control" / "schemas" / "project-structure.json").read_text(
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
            self.assertTrue((project / ".home-control" / "quote_items.jsonl").is_file())
            self.assertTrue((project / ".home-control" / "sites.jsonl").is_file())
            self.assertTrue((project / ".home-control" / "assets.jsonl").is_file())
            self.assertTrue((project / ".home-control" / "asset_events.jsonl").is_file())

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

    def test_real_v1_and_v2_projects_migrate_without_losing_existing_data(self) -> None:
        for version in ("1.0", "2.0"):
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
                self.assertIn("assets.jsonl", preview.stdout)
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
                self.assertEqual(after_marker["created_by"]["structure_version"], "3.0")
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
        for version, expected in (("1.5", "No supported migration"), ("4.0", "Refusing to downgrade")):
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

            audited = run_script(AUDIT, project)
            self.assertEqual(audited.returncode, 0, audited.stdout + audited.stderr)

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


if __name__ == "__main__":
    unittest.main()
