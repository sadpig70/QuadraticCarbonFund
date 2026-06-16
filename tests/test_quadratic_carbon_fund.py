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


class TestEngineUnits(unittest.TestCase):
    """Pure-function unit tests (no scenario dir needed)."""

    def test_verify_project_eligible_when_insufficient(self):
        p = {"project_id": "P", "name": "n", "location": "L",
             "emission_reduction": 10.0, "claimed_tco2": 100.0}
        self.assertEqual(qcf.verify_project(p), "eligible")

    def test_verify_project_verified_when_sufficient(self):
        p = {"project_id": "P", "name": "n", "location": "L",
             "emission_reduction": 80.0, "claimed_tco2": 100.0}
        self.assertEqual(qcf.verify_project(p), "verified")

    def test_compute_emission_reduction_capped_at_claim(self):
        p = {"location": "North", "generation_mwh": 500.0, "offset_mwh": 50.0, "claimed_tco2": 120.0}
        scen = {"emission_factor": [{"location": "North", "factor": "0.35"}],
                "params": {"offset_factor": 0.1}}
        # 500*0.35 - 50*0.1 = 170 -> min(claim 120, 170) = 120
        self.assertEqual(qcf.compute_emission_reduction(p, scen), 120.0)

    def test_emission_factor_default_fallback(self):
        table = [{"location": "default", "factor": "0.5"}]
        self.assertEqual(qcf.lookup_emission_factor("Unknown", table), 0.5)

    def test_qf_allocation_single_verified_takes_full_pool(self):
        state = {"scenario": {"params": {"matching_pool": 1000.0}},
                 "projects": [{"verdict": "verified", "emission_reduction": 100.0, "boost": 1.0}]}
        qcf.compute_qf_allocation(state)
        self.assertAlmostEqual(state["projects"][0]["raw_match"], 1000.0)

    def test_apply_caps_per_project(self):
        state = {"scenario": {"params": {"per_project_cap": 500.0}},
                 "projects": [{"raw_match": 1000.0, "verdict": "verified"}]}
        qcf.apply_caps(state)
        p = state["projects"][0]
        self.assertTrue(p["capped"])
        self.assertEqual(p["match"], 500.0)
        self.assertEqual(p["verdict"], "capped")

    def test_overall_verdict_precedence(self):
        self.assertEqual(qcf.overall_verdict([{"verdict": "matched"}, {"verdict": "capped"}]), "capped")
        self.assertEqual(qcf.overall_verdict([{"verdict": "matched"}, {"verdict": "eligible"}]), "matched")
        self.assertEqual(qcf.overall_verdict([{"verdict": "eligible"}]), "eligible")

    def test_reasons_for_capped_includes_policy(self):
        p = {"verdict": "capped", "emission_reduction": 100.0, "claimed_tco2": 120.0, "capped": True}
        reasons = qcf.reasons_for(p)
        self.assertTrue(any("verified reduction" in r for r in reasons))
        self.assertTrue(any("capped by policy" in r for r in reasons))


class TestScenarioVerdicts(unittest.TestCase):
    """End-to-end verdict coverage on the shipped example scenarios."""

    def _run_copy(self, src):
        tmp = tempfile.mkdtemp()
        dst = os.path.join(tmp, "scn")
        shutil.copytree(src, dst)
        try:
            state = qcf.output_layer(qcf.allocation_layer(
                qcf.verification_layer(qcf.input_layer(dst))))
            return state["output"]["verdict"]
        finally:
            shutil.rmtree(tmp)

    def test_example_matched(self):
        root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        self.assertEqual(self._run_copy(os.path.join(root, "examples", "matched")), "matched")

    def test_example_eligible(self):
        root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        self.assertEqual(self._run_copy(os.path.join(root, "examples", "eligible")), "eligible")


if __name__ == "__main__":
    unittest.main()
