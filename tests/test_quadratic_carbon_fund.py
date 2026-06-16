"""Tests for quadratic_carbon_fund.py — stdlib-only test runner compatible."""

import json
import os
import shutil
import sys
import tempfile
import unittest

# Ensure the parent package is importable.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import quadratic_carbon_fund as qcf


class TestIOLoaders(unittest.TestCase):
    def test_read_write_csv(self):
        rows = [{"a": "1", "b": "2"}, {"a": "3", "b": "4"}]
        with tempfile.NamedTemporaryFile("w", delete=False, suffix=".csv") as f:
            path = f.name
        try:
            qcf.write_csv(path, rows, ["a", "b"])
            loaded = qcf.read_csv(path)
            self.assertEqual(len(loaded), 2)
            self.assertEqual(loaded[0]["a"], "1")
        finally:
            os.unlink(path)

    def test_read_yaml(self):
        with tempfile.NamedTemporaryFile("w", delete=False, suffix=".yaml") as f:
            f.write("x: 10\ny: 2.5\nz: true\n")
            path = f.name
        try:
            data = qcf.read_yaml(path)
            self.assertEqual(data["x"], 10)
            self.assertEqual(data["y"], 2.5)
            self.assertEqual(data["z"], True)
        finally:
            os.unlink(path)


class TestHashChain(unittest.TestCase):
    def test_ledger_integrity(self):
        with tempfile.TemporaryDirectory() as tmp:
            ledger_path = os.path.join(tmp, "ledger.jsonl")
            qcf.append_jsonl(ledger_path, {"run_id": "r1", "hash": "abc"})
            qcf.append_jsonl(ledger_path, {"run_id": "r2", "hash": "def"})
            records = qcf.read_jsonl(ledger_path)
            self.assertEqual(len(records), 2)


class TestEndToEnd(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.scenario_dir = os.path.join(self.tmp, "scenario")
        os.makedirs(self.scenario_dir)
        qcf.write_csv(
            os.path.join(self.scenario_dir, "projects.csv"),
            [
                {
                    "project_id": "P001",
                    "name": "Solar",
                    "location": "North",
                    "claimed_tco2": "100",
                    "generation_mwh": "400",
                    "offset_mwh": "0",
                },
                {
                    "project_id": "P002",
                    "name": "Wind",
                    "location": "West",
                    "claimed_tco2": "60",
                    "generation_mwh": "200",
                    "offset_mwh": "0",
                },
                {
                    "project_id": "P003",
                    "name": "BadClaim",
                    "location": "South",
                    "claimed_tco2": "100",
                    "generation_mwh": "10",
                    "offset_mwh": "0",
                },
            ],
            ["project_id", "name", "location", "claimed_tco2", "generation_mwh", "offset_mwh"],
        )
        qcf.write_csv(
            os.path.join(self.scenario_dir, "emission_factor.csv"),
            [{"location": "North", "factor": "0.5"}, {"location": "West", "factor": "0.5"}, {"location": "South", "factor": "0.5"}],
            ["location", "factor"],
        )
        qcf.write_csv(
            os.path.join(self.scenario_dir, "fuel_mix.csv"),
            [{"fuel": "solar", "share": "0.5"}],
            ["fuel", "share"],
        )
        qcf.write_csv(
            os.path.join(self.scenario_dir, "demand.csv"),
            [{"hour": "1", "demand_mwh": "100"}],
            ["hour", "demand_mwh"],
        )
        qcf.write_csv(
            os.path.join(self.scenario_dir, "generation.csv"),
            [{"hour": "1", "source": "solar", "mwh": "100"}],
            ["hour", "source", "mwh"],
        )
        with open(os.path.join(self.scenario_dir, "params.yaml"), "w", encoding="utf-8") as f:
            f.write("matching_pool: 1000\nper_project_cap: 300\noffset_factor: 0.0\n")

    def tearDown(self):
        shutil.rmtree(self.tmp)

    def test_run_determinism_and_verdicts(self):
        state1 = qcf.input_layer(self.scenario_dir)
        state1 = qcf.verification_layer(state1)
        state1 = qcf.allocation_layer(state1)
        state1 = qcf.output_layer(state1)

        state2 = qcf.input_layer(self.scenario_dir)
        state2 = qcf.verification_layer(state2)
        state2 = qcf.allocation_layer(state2)
        state2 = qcf.output_layer(state2)

        self.assertEqual(state1["run_id"], state2["run_id"])
        self.assertEqual(state1["output"], state2["output"])

        out = state1["output"]
        by_id = {p["project_id"]: p for p in out["projects"]}
        self.assertEqual(by_id["P001"]["verdict"], "capped")
        self.assertEqual(by_id["P002"]["verdict"], "matched")
        self.assertEqual(by_id["P003"]["verdict"], "eligible")
        self.assertLessEqual(by_id["P001"]["match"], 600)
        self.assertEqual(out["verdict"], "capped")

    def test_ledger_verify(self):
        state = qcf.input_layer(self.scenario_dir)
        state = qcf.verification_layer(state)
        state = qcf.allocation_layer(state)
        state = qcf.output_layer(state)
        ledger_path = os.path.join(self.scenario_dir, "ledger.jsonl")
        result = qcf.verify_ledger(ledger_path)
        self.assertTrue(result["valid"])
        self.assertGreater(result["records"], 0)

        # Tamper with ledger
        with open(ledger_path, "r", encoding="utf-8") as f:
            lines = f.readlines()
        tampered = json.loads(lines[0])
        tampered["match"] = 999999
        lines[0] = json.dumps(tampered) + "\n"
        with open(ledger_path, "w", encoding="utf-8") as f:
            f.writelines(lines)
        result = qcf.verify_ledger(ledger_path)
        self.assertFalse(result["valid"])


if __name__ == "__main__":
    unittest.main()
