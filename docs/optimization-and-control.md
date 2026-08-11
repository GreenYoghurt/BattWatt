# How the battery is controlled

Every strategy — PV self-consumption, price-quantile, or MPC — is a `Controller` that gets
called once per 15-minute interval and returns two numbers: how much energy it *wants* to send
to the battery (`to_battery`) and how much it *wants* to pull out (`from_battery`). Controllers
never touch the battery directly. `Simulator.run()` (the only place grid flow is computed —
see `CLAUDE.md`) passes those intentions to `Battery.step()`, which enforces the real physical
limits (capacity, charge/discharge power, round-trip efficiency) and reports back what actually
happened. The simulator then derives that interval's grid import/export from the fixed formula:

```
net_grid_energy = (production − consumption) − (to_battery − from_battery) + (to_grid − from_grid)
```

So a controller can ask for more than the battery can physically deliver — the shortfall simply
spills to the grid, exactly as it would with a real inverter. This split (strategy layer vs.
physics layer) is why every controller below can be described purely in terms of *decisions*,
without worrying about capacity or power limits — those are enforced identically underneath
regardless of which controller is active.

## PV self-consumption (`Controller_PV`)

The default, simplest strategy. Exposed in the web app as "PV Prioriteit."

1. Whatever consumption and production overlap in the same interval nets out directly (this
   happens regardless of the battery, and always happens first).
2. Leftover PV production (`net_energy > 0`) charges the battery, up to whichever is smaller:
   the remaining capacity (accounting for charge efficiency) or the max charge power.
3. A leftover deficit (`net_energy < 0`) discharges the battery, up to whichever is smaller: the
   stored energy (accounting for discharge efficiency) or the max discharge power.

It never charges from the grid and never discharges to the grid — it has no notion of price, so
it can't do arbitrage. It simply maximizes how much of your own solar you use yourself.

## Price-quantile rule-based (`Controller_price`)

Available at the simulation-engine/CLI level (`example.py`, the test suite) but **not currently
exposed in the web app's strategy selector**.

Each day is split into thresholds using the 20th and 80th percentile of that day's day-ahead
prices (`threshold_low`, `threshold_high`). Per interval:

- **Price ≤ daily 20th percentile:** charge as much as possible — first from any excess PV,
  then from the grid.
- **Price ≥ daily 80th percentile:** discharge as much as possible — first to cover home
  consumption (avoiding an expensive import), then export the rest to the grid.
- **Otherwise ("mid-price"):** fall back to pure self-consumption, identical to
  `Controller_PV` — buffer excess PV in, or cover a deficit from what's stored, but never trade
  with the grid.

Because the thresholds are computed per calendar day, this strategy only ever compares a moment
to *that day's own* price spread — it has no sense of "today is cheap relative to yesterday."

## Model Predictive Control (`Controller_MPC`)

Exposed in the web app as "Kosten Optimaal (MPC)." Builds and solves a linear program with
[Pyomo](https://www.pyomo.org/) + the [HiGHS](https://highs.dev/) solver (`appsi_highs`, via the
`highspy` package) over a rolling look-ahead window, re-solving periodically rather than every
step for performance.

### Decision variables (per timestep `t` in the horizon)

| Variable | Meaning | Bounds |
|---|---|---|
| `to_battery[t]` | energy sent to the battery | `0 … max_charge_kw * duration_hours` |
| `from_battery[t]` | energy drawn from the battery | `0 … max_discharge_kw * duration_hours` |
| `soc[t]` | state of charge | `0 … capacity_kwh` |
| `grid_import[t]` | energy bought from the grid | `≥ 0` |
| `grid_export[t]` | energy sold to the grid | `≥ 0` |

### Constraints

- **Energy balance**, every interval:
  `production + grid_import + from_battery == consumption + grid_export + to_battery`
- **State of charge**, carried forward with the same efficiency convention as the physical
  battery:
  `soc[t] = soc[t-1] + to_battery[t-1] * eta_charge − from_battery[t-1] / eta_discharge`

  This is a linear re-implementation of the same math as `Battery._charge()`/`_discharge()` —
  necessary to keep the model a linear program — not a call into the `Battery` class itself. In
  practice the two stay consistent because both apply `eta_charge` on the way in and divide by
  `eta_discharge` on the way out.

### Objective

Minimize total cost over the horizon:

```
buy_price[t]  = (day_ahead_price[t] + buying_fee + energy_tax) * (1 + 21% VAT)
sell_price[t] = (day_ahead_price[t] − selling_fee) * (1 + 21% VAT)

cost = Σ [ grid_import[t] * buy_price[t] − grid_export[t] * sell_price[t]
         + (to_battery[t] + from_battery[t]) * degradation_cost ]
```

The `degradation_cost` term (default `0.001` €/kWh of throughput) is a soft penalty that
discourages the optimizer from cycling the battery for negligible arbitrage gains — it is a
tunable heuristic, not a calibrated battery-wear or warranty model.

**The optimizer's internal price model always taxes gross grid import** — it does not thread
`Provider.net_metering` through `buy_price`/`sell_price` at all. Since the app always builds its
`Provider` with `net_metering=False` anyway (see
[Cost calculation](cost-calculation.md#net-metering-vs-gross-accounting)), this is consistent
with what the app actually bills today, but a net-metering-enabled `Provider` passed to
`Controller_MPC` from a script would still optimize as if taxed on gross import while
`BillingEngine` bills it on net import — the two would disagree.

### Horizon, re-optimization, and plan caching

The app calls `Controller_MPC` with `horizon_hours=24.0` and `reoptimize_every_hours=12.0`: each
solve looks 24 hours ahead, and a fresh solve is only triggered once every 12 hours. Between
solves, decisions for every remaining step in the current plan are served from an in-memory
`plan_cache` keyed by timestamp, computed once at solve time — the LP is not re-solved at every
15-minute step. This is purely a performance optimization; it does not change what a full
step-by-step re-solve would decide, since the plan for the full horizon was already computed.

If the solver fails, or fewer than two steps remain in the data, the controller falls back to a
pass-through (`return production, consumption`) — no battery use for that step.

### Perfect foresight

`Controller_MPC` builds its horizon directly from `self.full_df` — the actual historical
consumption, production, and price data for the upcoming window, not a forecast. In other
words, it optimizes as if it had a perfect crystal ball for the next 24 hours, every 12 hours.
Real-world forecast error (weather, load, and price-forecast uncertainty) is not modeled at all.
This is why the app shows a **"Realistische Besparing (80%)"** figure alongside the raw MPC
savings estimate: a flat 20% derating applied in `app.py`, not derived from any forecast-error
model — just a rule-of-thumb caution that a real deployed controller would capture less than the
theoretical optimum. See [Assumptions & limitations](assumptions-and-limitations.md).
