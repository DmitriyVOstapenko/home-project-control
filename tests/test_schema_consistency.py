from __future__ import annotations

import json
import re
import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
PLUGIN_ROOT = REPO_ROOT / "plugins" / "home-project-control"
STRUCTURE_FILE = PLUGIN_ROOT / "schemas" / "project-structure.json"
ONTOLOGY_FILE = PLUGIN_ROOT / "schemas" / "ontology.json"
PROPOSAL_CONTRACT_FILE = PLUGIN_ROOT / "schemas" / "proposal-review-contract.json"
SKILLS_ROOT = PLUGIN_ROOT / "skills"


def version_tuple(value: str) -> tuple[int, ...]:
    return tuple(int(part) for part in value.split("."))


class SchemaConsistencyTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.structure = json.loads(STRUCTURE_FILE.read_text(encoding="utf-8"))
        cls.ontology = json.loads(ONTOLOGY_FILE.read_text(encoding="utf-8"))
        cls.proposal_contract = json.loads(PROPOSAL_CONTRACT_FILE.read_text(encoding="utf-8"))

    def test_jsonl_registry_metadata_matches_ontology(self) -> None:
        registries = self.structure["jsonl_files"]
        entities = self.ontology["entities"]

        self.assertIsInstance(registries, dict)
        for registry, metadata in registries.items():
            entity = metadata["entity"]
            with self.subTest(registry=registry, entity=entity):
                self.assertIn(entity, entities)
                self.assertEqual(entities[entity]["registry"], registry)
                self.assertTrue(metadata["id_field"].endswith("_id"))
                self.assertIsInstance(metadata["append_only"], bool)

        for entity, definition in entities.items():
            registry = definition.get("registry")
            if isinstance(registry, str) and registry.endswith(".jsonl"):
                with self.subTest(entity=entity):
                    self.assertIn(registry, registries)
                    self.assertEqual(registries[registry]["entity"], entity)

    def test_registry_versions_and_migration_routes_are_coherent(self) -> None:
        current = self.structure["structure_version"]
        current_tuple = version_tuple(current)

        for registry, metadata in self.structure["jsonl_files"].items():
            with self.subTest(registry=registry):
                self.assertLessEqual(version_tuple(metadata["introduced_in"]), current_tuple)

        migrations = self.structure["supported_migrations"]
        self.assertTrue(migrations)
        for source, route in migrations.items():
            with self.subTest(source=source):
                self.assertLess(version_tuple(source), current_tuple)
                self.assertEqual(route["to"], current)
                self.assertEqual(route["mode"], "additive")

    def test_skill_folder_matches_frontmatter_name(self) -> None:
        skill_dirs = sorted(path for path in SKILLS_ROOT.iterdir() if path.is_dir())
        self.assertEqual(len(skill_dirs), 13)

        for skill_dir in skill_dirs:
            skill_file = skill_dir / "SKILL.md"
            with self.subTest(skill=skill_dir.name):
                text = skill_file.read_text(encoding="utf-8")
                match = re.search(r"(?m)^name:\s*([^\r\n]+)$", text)
                self.assertIsNotNone(match)
                self.assertEqual(match.group(1).strip(), skill_dir.name)

    def test_proposal_review_contract_is_unique_and_matches_ontology(self) -> None:
        contract = self.proposal_contract
        self.assertEqual(
            set(contract["check_statuses"]),
            set(self.ontology["dimensions"]["mandatory_check_status"]),
        )
        self.assertTrue(set(contract["ready_statuses"]).issubset(set(contract["check_statuses"])))
        for section, field in (
            ("universal_checks", "check_id"),
            ("discipline_axes", "axis_id"),
            ("technical_alternative_tracks", "track_id"),
        ):
            identifiers = [value[field] for value in contract[section]]
            with self.subTest(section=section):
                self.assertTrue(identifiers)
                self.assertEqual(len(identifiers), len(set(identifiers)))


if __name__ == "__main__":
    unittest.main()
