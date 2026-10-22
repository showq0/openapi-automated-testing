import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from scripts.openapi_to_bruno import convert, extract_constraints, generate_payload_scenarios


class ConvertTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.source = Path(self.temp_dir.name) / "api-spec.yaml"
        self.source.write_text("openapi: 3.0.0\n", encoding="utf-8")
        self.output = Path(self.temp_dir.name) / "collection"

    def tearDown(self):
        self.temp_dir.cleanup()

    @patch("scripts.openapi_to_bruno.shutil.which", return_value="/usr/bin/bru")
    @patch("scripts.openapi_to_bruno.generate_collection_scenarios", return_value=0)
    @patch("scripts.openapi_to_bruno.subprocess.run")
    @patch("scripts.openapi_to_bruno._yaml_module")
    @patch("scripts.openapi_to_bruno.load_spec", return_value={"paths": {}})
    def test_invokes_official_import_command(self, _load, _yaml, run, _generate, _which):
        convert(self.source, self.output, "Sample API")
        run.assert_called_once_with(
            [
                "/usr/bin/bru", "import", "openapi", "--source", str(self.source.resolve()),
                "--output", str(self.output.resolve()), "--collection-name", "Sample API",
            ],
            check=True,
        )

    def test_missing_source_is_rejected_before_invoking_cli(self):
        with self.assertRaises(FileNotFoundError):
            convert(self.source.with_name("missing.yaml"), self.output)

    @patch("scripts.openapi_to_bruno._yaml_module")
    @patch("scripts.openapi_to_bruno.shutil.which", return_value=None)
    @patch("scripts.openapi_to_bruno.load_spec", return_value={"paths": {}})
    def test_missing_cli_is_reported(self, _load, _which, _yaml):
        with self.assertRaisesRegex(RuntimeError, "Bruno CLI was not found"):
            convert(self.source, self.output)


class SchemaScenarioTests(unittest.TestCase):
    def test_extracts_nested_constraints_and_required_fields(self):
        schema = {
            "type": "object",
            "required": ["role"],
            "properties": {"role": {"type": "string", "enum": ["admin", "user"]}},
        }
        constraints = extract_constraints(schema)
        role = next(item for item in constraints if item["path"] == "$.role")
        self.assertTrue(role["constraints"]["required"])
        self.assertEqual(role["constraints"]["enum"], ["admin", "user"])

    def test_generates_valid_payload_and_invalid_enum_and_required_cases(self):
        schema = {
            "type": "object",
            "required": ["role"],
            "properties": {"role": {"type": "string", "enum": ["admin", "user"]}},
        }
        scenarios = generate_payload_scenarios(schema, {})
        self.assertTrue(scenarios[0]["valid"])
        self.assertEqual(scenarios[0]["payload"], {"role": "admin"})
        self.assertTrue(any("Missing required" in case["name"] for case in scenarios))
        self.assertTrue(any("Invalid enum" in case["name"] for case in scenarios))


if __name__ == "__main__":
    unittest.main()
