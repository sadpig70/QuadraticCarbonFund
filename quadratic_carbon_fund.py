"""QuadraticCarbonFund — deterministic carbon-credit matching via quadratic funding.

A stdlib-only CLI that allocates a carbon-credit matching pool to community projects
using verifiable emission reductions as the quadratic signal.

Commands:
    sample                Print example scenario files to stdout.
    run <scenario_dir>    Compute allocation and write state.json + ledger.jsonl.
    report <state.json>   Render Markdown report from a state file.
    verify <ledger.jsonl> Verify append-only hash-chain integrity.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
import os
import sys
from typing import Any

__version__ = "0.1.0"


# ---------------------------------------------------------------------------
# Basic I/O utilities (stdlib only)
# ---------------------------------------------------------------------------

def read_csv(path: str) -> list[dict[str, str]]:
    """Read a CSV file into a list of dictionaries."""
    with open(path, "r", encoding="utf-8", newline="") as f:
        reader = csv.DictReader(f)
        return [row for row in reader]


def write_csv(path: str, rows: list[dict[str, Any]], fieldnames: list[str]) -> None:
    """Write a list of dictionaries to a CSV file."""
    with open(path, "w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        for row in rows:
            writer.writerow({k: str(row.get(k, "")) for k in fieldnames})


def read_yaml(path: str) -> dict[str, Any]:
    """Minimal YAML loader for flat key: value files.

    Supports:
      - strings (no quotes, single quotes, double quotes)
      - ints, floats
      - booleans: true/false/yes/no
      - null/None
    """
    result: dict[str, Any] = {}
    with open(path, "r", encoding="utf-8") as f:
        for raw in f:
            line = raw.strip()
            if not line or line.startswith("#"):
                continue
            if ":" not in line:
                continue
            key, _, value = line.partition(":")
            key = key.strip()
            value = value.strip()
            result[key] = _parse_yaml_value(value)
    return result


def _parse_yaml_value(value: str) -> Any:
    value = value.strip()
    if value == "":
        return None
    lower = value.lower()
    if lower in ("true", "yes"):
        return True
    if lower in ("false", "no"):
        return False
    if lower in ("null", "none", "~"):
        return None
    # quoted strings
    if (value.startswith('"') and value.endswith('"')) or (
        value.startswith("'") and value.endswith("'")
    ):
        return value[1:-1]
    # int
    try:
        return int(value)
    except ValueError:
        pass
    # float
    try:
        return float(value)
    except ValueError:
        pass
    return value


def load_json(path: str) -> Any:
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def write_json(path: str, obj: Any) -> None:
    with open(path, "w", encoding="utf-8") as f:
        json.dump(obj, f, indent=2, ensure_ascii=False)
        f.write("\n")


def append_jsonl(path: str, obj: Any) -> None:
    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps(obj, ensure_ascii=False, sort_keys=True) + "\n")


def read_jsonl(path: str) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                records.append(json.loads(line))
    return records


# ---------------------------------------------------------------------------
# Hashing
# ---------------------------------------------------------------------------

def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def sha256_json(obj: Any) -> str:
    return sha256_text(json.dumps(obj, ensure_ascii=False, sort_keys=True, separators=(",", ":")))


def make_run_id(projects: list[dict[str, Any]], scenario: dict[str, Any]) -> str:
    """Deterministic run id from input content hash."""
    payload = json.dumps(
        {"projects": projects, "scenario": scenario},
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return sha256_text(payload)[:16]


# ---------------------------------------------------------------------------
# Input layer
# ---------------------------------------------------------------------------

def parse_float(value: str) -> float:
    try:
        return float(value)
    except (ValueError, TypeError) as exc:
        raise ValueError(f"cannot parse as float: {value!r}") from exc


def load_project_registry(path: str) -> list[dict[str, Any]]:
    rows = read_csv(path)
    required = {"project_id", "name", "location", "claimed_tco2", "generation_mwh", "offset_mwh"}
    for row in rows:
        if not required.issubset(row.keys()):
            raise ValueError(
                f"missing required field in project {row.get('project_id', '?')}: "
                f"expected {required}, got {set(row.keys())}"
            )
        row["claimed_tco2"] = parse_float(row["claimed_tco2"])
        row["generation_mwh"] = parse_float(row["generation_mwh"])
        row["offset_mwh"] = parse_float(row["offset_mwh"])
    return rows


def load_scenario(scenario_dir: str) -> dict[str, Any]:
    demand = read_csv(os.path.join(scenario_dir, "demand.csv"))
    generation = read_csv(os.path.join(scenario_dir, "generation.csv"))
    emission_factor = read_csv(os.path.join(scenario_dir, "emission_factor.csv"))
    fuel_mix = read_csv(os.path.join(scenario_dir, "fuel_mix.csv"))
    params = read_yaml(os.path.join(scenario_dir, "params.yaml"))
    return {
        "demand": demand,
        "generation": generation,
        "emission_factor": emission_factor,
        "fuel_mix": fuel_mix,
        "params": params,
    }


def input_layer(scenario_dir: str) -> dict[str, Any]:
    projects = load_project_registry(os.path.join(scenario_dir, "projects.csv"))
    scenario = load_scenario(scenario_dir)
    return {
        "scenario_dir": scenario_dir,
        "projects": projects,
        "scenario": scenario,
        "run_id": make_run_id(projects, scenario),
    }


# ---------------------------------------------------------------------------
# Verification layer
# ---------------------------------------------------------------------------

def lookup_emission_factor(location: str, table: list[dict[str, str]]) -> float:
    for row in table:
        if row.get("location") == location:
            return parse_float(row.get("factor", "0"))
    # Default fallback: use a global factor if present.
    for row in table:
        if row.get("location") == "default":
            return parse_float(row.get("factor", "0"))
    return 0.0


def compute_emission_reduction(project: dict[str, Any], scenario: dict[str, Any]) -> float:
    """Deterministic, simplified emission reduction score."""
    ef = lookup_emission_factor(project["location"], scenario["emission_factor"])
    offset_factor = float(scenario["params"].get("offset_factor", 0.0))
    theoretical = project["generation_mwh"] * ef - project["offset_mwh"] * offset_factor
    return min(project["claimed_tco2"], max(0.0, theoretical))


def verify_project(project: dict[str, Any]) -> str:
    """Return 'eligible' or 'verified'."""
    required_present = all(
        project.get(k) not in (None, "", 0, 0.0) for k in ["project_id", "name", "location"]
    )
    if not required_present:
        return "eligible"
    if project["emission_reduction"] <= 0:
        return "eligible"
    if project["emission_reduction"] < project["claimed_tco2"] * 0.5:
        return "eligible"
    return "verified"


def verification_layer(state: dict[str, Any]) -> dict[str, Any]:
    for p in state["projects"]:
        p["emission_reduction"] = compute_emission_reduction(p, state["scenario"])
        p["verdict"] = verify_project(p)
    return state


# ---------------------------------------------------------------------------
# Allocation layer
# ---------------------------------------------------------------------------

def apply_boosts(state: dict[str, Any]) -> dict[str, Any]:
    boost_path = os.path.join(state["scenario_dir"], "boost.csv")
    boosts = read_csv(boost_path) if os.path.exists(boost_path) else []
    boost_map: dict[str, float] = {}
    for b in boosts:
        pid = b.get("project_id")
        if pid:
            boost_map[pid] = parse_float(b.get("boost", "1.0"))
    for p in state["projects"]:
        p["boost"] = boost_map.get(p["project_id"], 1.0)
    return state


def compute_qf_allocation(state: dict[str, Any]) -> dict[str, Any]:
    pool = float(state["scenario"]["params"]["matching_pool"])
    total_score = 0.0
    for p in state["projects"]:
        if p["verdict"] == "verified":
            p["score"] = math.sqrt(p["emission_reduction"] * p["boost"])
            total_score += p["score"]
        else:
            p["score"] = 0.0
    total_squared = total_score ** 2 if total_score > 0 else 0.0
    for p in state["projects"]:
        if total_squared > 0 and p["score"] > 0:
            p["raw_match"] = pool * (p["score"] ** 2) / total_squared
        else:
            p["raw_match"] = 0.0
    return state


def apply_caps(state: dict[str, Any]) -> dict[str, Any]:
    params = state["scenario"]["params"]
    per_project_cap = params.get("per_project_cap")
    total_cap = params.get("total_matching_pool")
    for p in state["projects"]:
        p["capped"] = False
        if per_project_cap is not None and p["raw_match"] > per_project_cap:
            p["raw_match"] = float(per_project_cap)
            p["capped"] = True
    if total_cap is not None:
        current_total = sum(p["raw_match"] for p in state["projects"])
        if current_total > total_cap:
            factor = total_cap / current_total
            for p in state["projects"]:
                p["raw_match"] *= factor
                p["capped"] = True
    for p in state["projects"]:
        p["match"] = round(p["raw_match"], 6)
        if p["verdict"] == "verified":
            p["verdict"] = "matched" if not p["capped"] else "capped"
    return state


def allocation_layer(state: dict[str, Any]) -> dict[str, Any]:
    state = apply_boosts(state)
    state = compute_qf_allocation(state)
    state = apply_caps(state)
    return state


# ---------------------------------------------------------------------------
# Output layer
# ---------------------------------------------------------------------------

def reasons_for(project: dict[str, Any]) -> list[str]:
    reasons: list[str] = []
    if project["verdict"] in ("matched", "capped"):
        reasons.append(
            f"verified reduction {project['emission_reduction']:.2f} tCO2 "
            f"(claim {project['claimed_tco2']:.2f})"
        )
    else:
        reasons.append(
            f"not verified: reduction {project['emission_reduction']:.2f} "
            f"is insufficient vs claim {project['claimed_tco2']:.2f}"
        )
    if project["capped"]:
        reasons.append("match capped by policy")
    return reasons


def overall_verdict(projects: list[dict[str, Any]]) -> str:
    # Capped takes precedence because it signals policy intervention.
    if any(p["verdict"] == "capped" for p in projects):
        return "capped"
    if any(p["verdict"] == "matched" for p in projects):
        return "matched"
    if any(p["verdict"] == "verified" for p in projects):
        return "verified"
    return "eligible"


def collect_all_reasons(projects: list[dict[str, Any]]) -> list[str]:
    reasons: list[str] = []
    for p in projects:
        reasons.extend([f"{p['project_id']}: {r}" for r in reasons_for(p)])
    return reasons


def build_state(state: dict[str, Any]) -> dict[str, Any]:
    output = {
        "run_id": state["run_id"],
        "verdict": overall_verdict(state["projects"]),
        "matching_pool": float(state["scenario"]["params"]["matching_pool"]),
        "projects": [
            {
                "project_id": p["project_id"],
                "name": p["name"],
                "location": p["location"],
                "verdict": p["verdict"],
                "claimed_tco2": p["claimed_tco2"],
                "verified_tco2": round(p["emission_reduction"], 6),
                "boost": p["boost"],
                "match": p["match"],
                "capped": p["capped"],
                "reasons": reasons_for(p),
            }
            for p in state["projects"]
        ],
        "reasons": collect_all_reasons(state["projects"]),
    }
    state["output"] = output
    return state


def last_hash(ledger_path: str) -> str:
    if not os.path.exists(ledger_path):
        return "0" * 64
    records = read_jsonl(ledger_path)
    if not records:
        return "0" * 64
    return records[-1].get("hash", "0" * 64)


def write_ledger(state: dict[str, Any]) -> None:
    ledger_path = os.path.join(state["scenario_dir"], "ledger.jsonl")
    prev_hash = last_hash(ledger_path)
    for p in state["projects"]:
        if p["verdict"] in ("matched", "capped"):
            record = {
                "run_id": state["run_id"],
                "project_id": p["project_id"],
                "verified_tco2": round(p["emission_reduction"], 6),
                "match": p["match"],
                "previous_hash": prev_hash,
            }
            record["hash"] = sha256_json(record)
            append_jsonl(ledger_path, record)
            prev_hash = record["hash"]


def render_report(state: dict[str, Any]) -> str:
    out = state["output"]
    lines = [
        "# QuadraticCarbonFund Allocation Report",
        "",
        f"- **Run ID**: `{out['run_id']}`",
        f"- **Overall verdict**: `{out['verdict']}`",
        f"- **Matching pool**: {out['matching_pool']:.2f}",
        "",
        "## Projects",
        "",
        "| Project | Location | Verdict | Claimed tCO2 | Verified tCO2 | Boost | Match | Capped |",
        "|---|---|---|---:|---:|---:|---:|:---:|",
    ]
    total_match = 0.0
    for p in out["projects"]:
        total_match += p["match"]
        lines.append(
            f"| {p['name']} | {p['location']} | `{p['verdict']}` | "
            f"{p['claimed_tco2']:.2f} | {p['verified_tco2']:.2f} | "
            f"{p['boost']:.2f} | {p['match']:.2f} | {'Yes' if p['capped'] else 'No'} |"
        )
    lines.extend([
        "",
        f"**Total matched**: {total_match:.2f}",
        "",
        "## Reasons",
        "",
    ])
    for r in out["reasons"]:
        lines.append(f"- {r}")
    return "\n".join(lines) + "\n"


def output_layer(state: dict[str, Any]) -> dict[str, Any]:
    state = build_state(state)
    write_ledger(state)
    return state


# ---------------------------------------------------------------------------
# Ledger verification
# ---------------------------------------------------------------------------

def verify_ledger(ledger_path: str) -> dict[str, Any]:
    records = read_jsonl(ledger_path)
    if not records:
        return {"valid": True, "records": 0, "errors": ["empty ledger"]}
    errors: list[str] = []
    prev_hash = "0" * 64
    for i, rec in enumerate(records):
        if rec.get("previous_hash") != prev_hash:
            errors.append(f"record {i}: previous_hash mismatch")
        recomputed = sha256_json({k: v for k, v in rec.items() if k != "hash"})
        if rec.get("hash") != recomputed:
            errors.append(f"record {i}: hash mismatch")
        prev_hash = rec.get("hash", "")
    return {"valid": len(errors) == 0, "records": len(records), "errors": errors}


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def cmd_sample() -> int:
    print(EXAMPLE_SCENARIO)
    return 0


def cmd_run(scenario_dir: str) -> int:
    state = input_layer(scenario_dir)
    state = verification_layer(state)
    state = allocation_layer(state)
    state = output_layer(state)
    state_path = os.path.join(scenario_dir, "state.json")
    write_json(state_path, state["output"])
    print(json.dumps(state["output"], indent=2, ensure_ascii=False))
    return 0


def cmd_report(state_file: str) -> int:
    state = {"output": load_json(state_file)}
    print(render_report(state))
    return 0


def cmd_verify(ledger_file: str) -> int:
    result = verify_ledger(ledger_file)
    print(json.dumps(result, indent=2, ensure_ascii=False))
    return 0 if result["valid"] and not result.get("errors") == ["empty ledger"] else 1


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="quadratic_carbon_fund",
        description="Allocate a carbon-credit matching pool via quadratic funding.",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("sample", help="Print example scenario files.")

    run_parser = sub.add_parser("run", help="Run allocation for a scenario directory.")
    run_parser.add_argument("scenario_dir", help="Path to scenario directory.")

    report_parser = sub.add_parser("report", help="Render Markdown report from state.json.")
    report_parser.add_argument("state_file", help="Path to state.json.")

    verify_parser = sub.add_parser("verify", help="Verify ledger.jsonl hash chain.")
    verify_parser.add_argument("ledger_file", help="Path to ledger.jsonl.")

    args = parser.parse_args(argv)

    if args.command == "sample":
        return cmd_sample()
    if args.command == "run":
        return cmd_run(args.scenario_dir)
    if args.command == "report":
        return cmd_report(args.state_file)
    if args.command == "verify":
        return cmd_verify(args.ledger_file)
    return 1


EXAMPLE_SCENARIO = r"""# QuadraticCarbonFund example scenario
# Save these three files into a directory (e.g. examples/baseline/):

# projects.csv
project_id,name,location,claimed_tco2,generation_mwh,offset_mwh
P001,Community Solar Alpha,North,120.0,500.0,50.0
P002,Wind Co-op Beta,West,80.0,300.0,20.0
P003,Microgrid Gamma,South,10.0,10.0,5.0
P004,Efficiency Retrofit Delta,East,200.0,400.0,0.0

# emission_factor.csv
location,factor
North,0.35
West,0.40
South,0.45
East,0.50
default,0.40

# fuel_mix.csv
fuel,share
solar,0.3
wind,0.3
gas,0.4

# demand.csv
hour,demand_mwh
1,100
2,110

# generation.csv
hour,source,mwh
1,solar,30
1,wind,30
1,gas,40
2,solar,35
2,wind,35
2,gas,40

# params.yaml
matching_pool: 10000
per_project_cap: 1000
total_matching_pool: 10000
offset_factor: 0.1

# boost.csv (optional)
project_id,boost
P001,1.2
P002,1.0
"""


if __name__ == "__main__":
    sys.exit(main())
