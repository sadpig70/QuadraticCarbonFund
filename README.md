# QuadraticCarbonFund

> Allocate a carbon-credit matching pool to community projects using verifiable emission reductions and quadratic funding weights.

## One-sentence pitch

`QuadraticCarbonFund` answers: *Can quadratic funding allocate a carbon-credit matching pool to community projects based on verifiable emission reductions?*

## Why this matters

Traditional quadratic funding uses donations as the signal for matching. `QuadraticCarbonFund` substitutes that signal with a deterministic, reproducible emission-reduction calculation. This makes the matching pool allocation transparent, auditable, and grounded in physical climate impact rather than popularity or wealth.

## What it is not

- Not a carbon offset registry.
- Not a carbon market exchange.
- Not a full power-system dispatch simulator.
- Not a legal carbon accounting tool.

It is a stdlib-only allocator that matches a limited carbon-credit pool to community projects using quadratic weights derived from a simplified, deterministic emission-reduction scenario.

## Install / Run

Requires Python 3.10+ and no external packages.

```bash
python quadratic_carbon_fund.py sample > examples/baseline/README.txt
python quadratic_carbon_fund.py run examples/baseline
python quadratic_carbon_fund.py report examples/baseline/state.json
python quadratic_carbon_fund.py verify examples/baseline/ledger.jsonl
```

## Scenario format

A scenario directory contains:

- `projects.csv` — project registry with `project_id`, `name`, `location`, `claimed_tco2`, `generation_mwh`, `offset_mwh`.
- `emission_factor.csv` — `location`, `factor` for emission reduction calculation.
- `fuel_mix.csv` — `fuel`, `share` (contextual data, currently informational).
- `demand.csv` — `hour`, `demand_mwh` (contextual data, currently informational).
- `generation.csv` — `hour`, `source`, `mwh` (contextual data, currently informational).
- `params.yaml` — `matching_pool`, optional `per_project_cap`, optional `total_matching_pool`, `offset_factor`.
- `boost.csv` (optional) — `project_id`, `boost` for signal weighting.

## Verdict scheme

- `eligible` — basic participation rules met, but emission reduction could not be verified.
- `verified` — emission reduction is positive and credible (> 50% of claimed).
- `capped` — verified and received a match, but the match was reduced by a policy cap.
- `matched` — verified and received the full quadratic funding allocation.

## Quadratic funding formula

For each verified project `p`:

```text
score_p = sqrt(verified_tco2_p * boost_p)
match_p = matching_pool * score_p^2 / sum(score_q^2)
```

Only verified projects participate in the denominator.

## Ledger

Each `run` appends matching records to `ledger.jsonl` as an append-only hash chain. `verify` checks chain integrity.

## Tests

```bash
python -m pytest tests/
```

Or run the stdlib test file directly:

```bash
python tests/test_quadratic_carbon_fund.py
```

## License

MIT License — see [LICENSE](LICENSE).
