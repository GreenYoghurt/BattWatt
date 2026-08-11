# Assumptions & known limitations

BattWatt is a simulation and estimation tool, not a substitute for a formal energy audit. Every
number it produces rests on a set of simplifications, listed here so results can be interpreted
correctly. Each item points at the code it comes from.

## PV curtailment is not modeled — a battery can never "solve" it here

This is the most important limitation to understand before trusting a savings estimate for a
solar installation that experiences curtailment.

In reality, inverters or the grid operator sometimes cap ("curtail") how much solar power is
allowed to flow to the grid — because of an inverter export limit, grid congestion, or negative
prices making export uneconomical. BattWatt cannot represent this, for two independent reasons:

1. **No export cap exists anywhere in the physics model.** Neither `Battery` (`battery.py`) nor
   `Simulator` (`simulator.py`) impose any limit on `to_grid`/export power. Production always
   flows to the grid at whatever level results from the meter data and the battery's decisions —
   there is no parameter for an inverter or connection export limit.
2. **Curtailed energy is invisible to the tool.** The `teruglevering` column comes from your
   meter export — i.e. whatever was *actually* exported after any real-world curtailment already
   happened. If your existing system curtails 5% of its production today, that 5% was never
   measured and never enters the simulation. Adding a battery to the simulation can only
   reallocate energy that's present in the recorded data; it can't recover energy that was
   already clipped upstream of the meter.

**Practical effect:** if your installation is curtailment-prone, BattWatt will systematically
*underestimate* the value a battery would add, because "storing energy that would otherwise be
curtailed" is exactly the scenario it cannot see or simulate.

## Market & contract assumptions

- **Dynamic contracts only, by default.** The web app always builds its `Provider` with
  `net_metering=False` (`app.py`), modeling the post-salderingsregeling world. `Provider` and
  `BillingEngine` do support the current net-metering regime (`net_metering=True`), but there is
  no UI control to switch to it — see
  [Cost calculation](cost-calculation.md#net-metering-vs-gross-accounting).
- **ENTSO-E day-ahead prices are used as-is**, with markups/tax/VAT applied on top
  (`energy_providers.py`). Real dynamic contracts may include additional imbalance costs,
  hedging/portfolio fees, or settle on a different granularity than the modeled 15-minute
  interval — none of that is represented.
- **Negative prices are not clipped.** This is realistic (dynamic contracts genuinely expose you
  to negative prices), but it means purchase costs or feed-in revenue can each go negative for
  an interval.

## Tariffs are locked to 2025, and to a hardcoded DSO

`get_energy_tax_excl_vat()` and `get_tax_discount()` (`energy_providers.py`) only have data for
2025 and raise a `ValueError` for any other year. The grid operator fee defaults to Enexis
(`BillingEngine(dso='Enexis')`) and **the web app never exposes a DSO selector**, so every
simulation run through `app.py` uses Enexis 2025 tariffs for `netbeheerskosten`, regardless of
your actual grid operator. Other DSOs (Liander, Stedin, Westland Infra, Coteq, RENDO) have
constants defined but aren't reachable from the UI.

## Fixed costs assume a full year of data

`BillingEngine.calculate_bill()` always applies the full-year value of subscription cost, grid
operator fee, and tax credit — it does not prorate them to the length of the dataset you upload.
Simulating three months of data still charges/credits a full year's worth of fixed costs. Only
the flexible (per-kWh) costs scale correctly with the amount of data provided. See
[Cost calculation](cost-calculation.md#fixed-annual-costs).

## Battery physics are simplified

- **Constant round-trip efficiency**, configured as a single "netto rendement gehele
  installatie" figure (default 90%, used by every preset battery as well as the default for a
  custom one) rather than separate charge/discharge numbers. `Battery` splits it evenly across
  the two legs internally (`sqrt(efficiency)` each way, so the two legs' product reproduces the
  configured round-trip value), but there's no dependence on temperature, charge rate, or state
  of charge.
- **No calendar or cycle aging.** Capacity does not degrade over the simulated period, however
  long it is.
- **Constant standby draw** (default 10 W) models the inverter/BMS's own idle consumption. It's
  added to household consumption every interval — covered by PV first, then by the battery
  itself, then by the grid — the same priority order as any other load. It is not itself
  affected by the round-trip efficiency above.
- **No minimum state of charge / depth-of-discharge floor**, and no replacement-cost or warranty
  economics — a battery can be cycled between 0% and 100% indefinitely at no cost beyond the
  MPC's optional degradation penalty (see below).
- **The MPC's `degradation_cost`** (default €0.001/kWh throughput) is a tunable heuristic added
  to discourage needless cycling, not a calibrated wear/warranty model. It has no effect on the
  PV or price-quantile controllers, which don't optimize against a cost function at all.

## MPC uses perfect foresight

`Controller_MPC` optimizes against the actual historical production, consumption, and price data
for its look-ahead window — not a forecast. It behaves as though it always knows the next 24
hours exactly. The app's "Realistische Besparing (80%)" figure is a flat, uncalibrated 20%
haircut meant to caution that a real deployed controller (which must forecast, not know, the
future) would likely capture less than the theoretical optimum — it is not derived from any
forecast-error model. See
[Optimization & control](optimization-and-control.md#perfect-foresight).

## Data assumptions

- **15-minute (kwartier) interval data is expected throughout.** `Simulator.run()` defaults to
  `duration_hours=0.25`, and the loaders/checks assume roughly consistent interval spacing.
- **Meter and price data are matched by nearest timestamp** within a 15-minute tolerance
  (`merge_data()` in `data_loader.py`). Intervals that don't have a price within that tolerance
  are silently dropped from the simulation.
- **Data quality checks are limited.** `data_checks.py` currently only flags one failure mode
  (HomeWizard CSVs corrupted by an Excel round-trip) and only for loaders that expose raw L1/L2/L3
  columns; most format/loader combinations run without any automated sanity check.

## Scope

- **Single connection only.** There's no support for netting energy across multiple physical
  grid connections/meters at one address.
- **No grid congestion or capacity tariffs** (e.g. a `capaciteitstarief`) are modeled — only the
  fixed netbeheerskosten described above.
- **Privacy, not accuracy, is what's guaranteed to hold:** the app processes everything
  in-memory for the session and stores nothing server-side (see the privacy notice in the app
  header) — but that's a data-handling guarantee, independent of every modeling assumption above.
