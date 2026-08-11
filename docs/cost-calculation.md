# How costs are calculated

BattWatt estimates a full-year electricity bill twice — once for the situation as measured
(no battery) and once for each simulated battery — and reports the difference as "savings."
Both bills are built from the same two ingredients:

- **Fixed annual costs**, independent of how much energy you use.
- **Flexible costs**, driven by every 15-minute interval's consumption, feed-in, and market price.

All formulas below live in [`energy_providers.py`](../energy_providers.py) (`Provider` class and
the tariff constant functions) and are orchestrated by [`billing.py`](../billing.py)'s
`BillingEngine`. `BillingEngine.calculate_bill_breakdown()` returns every line item shown in the
app's "Kostenopbouw" tables, so the code and the UI use exactly the same numbers.

## Fixed annual costs

| Component | Source | 2025 value |
|---|---|---|
| Abonnementskosten (subscription) | `Provider.subscription_cost` — user input | e.g. €75/year |
| Netbeheerskosten (grid operator fee) | `get_grid_operator_fee(dso)` | €475.92/year (Enexis) |
| Belastingvermindering (tax credit) | `get_tax_discount(year)` | −€635.19/year |

```
fixed_costs = abonnementskosten + netbeheerskosten − belastingvermindering
```

The tax credit (`belastingvermindering`) is the standard Dutch flat rebate every private
electricity connection receives, applied once regardless of consumption. The grid operator fee
is a flat annual amount set by your DSO (distribution system operator), not usage-based.

> **Important:** `BillingEngine.calculate_bill()` always uses the **full-year** value of these
> three components — it does not prorate them to the length of the data you uploaded. If you
> simulate three months instead of a full year, the fixed-cost portion of the bill will still
> reflect a full year, which will skew any total-bill comparison for partial-year datasets. The
> flexible costs below scale correctly with however much data you provide; the fixed costs do
> not. See [Assumptions & limitations](assumptions-and-limitations.md).

## Flexible costs (per interval)

Every 15-minute interval contributes to five running totals, each summed over the whole
dataset and computed by `Provider.calculate_flexible_costs_breakdown()`:

1. **Marktprijs inkoop** (wholesale purchase cost) — you pay the ENTSO-E day-ahead price for
   every kWh you draw from the grid, VAT included:
   ```
   marktprijs_inkoop = (1 + 21% VAT) * Σ(consumption_kwh[t] * day_ahead_price[t])
   ```
2. **Energiebelasting** (energy tax) — a flat €0.1088/kWh (2025, brackets 1 & 2 combined),
   VAT included. **This is where net metering (salderingsregeling) changes the outcome** — see
   below.
3. **Leveranciersopslag inkoop** (supplier purchase mark-up) — `Provider.buying_fee` per kWh
   consumed. Entered in the app **VAT-inclusive**, so it's added as-is (unlike the wholesale
   price, which is VAT-excl. and gets `* 1.21` applied explicitly).
4. **Leveranciersopslag teruglevering** (supplier feed-in mark-up) — `Provider.selling_fee`
   per kWh exported, also VAT-inclusive, and also a *cost* (it reduces your feed-in revenue).
5. **Teruglevering opbrengst** (feed-in revenue) — you're paid the day-ahead price for every kWh
   exported, VAT included:
   ```
   teruglevering_opbrengst = (1 + 21% VAT) * Σ(feed_in_kwh[t] * day_ahead_price[t])
   ```

```
flexible_costs = marktprijs_inkoop + energiebelasting
                + leveranciersopslag_inkoop + leveranciersopslag_verkoop
                − teruglevering_opbrengst

total_bill = fixed_costs + flexible_costs
```

Day-ahead prices can be negative (common on windy, sunny days with low demand). Nothing in the
formulas clips negative prices, so in principle `marktprijs_inkoop` or
`teruglevering_opbrengst` can each go negative for an interval — this is a realistic feature of
dynamic contracts, not a bug.

### Net metering vs. gross accounting

`Provider.net_metering` controls how `energiebelasting` is computed — the single most
financially significant switch in the codebase:

- **`net_metering=True`** (salderingsregeling): tax is levied on **net annual consumption**,
  i.e. `max(0, total_consumption − total_feed_in)`. Feed-in can fully cancel out consumption for
  tax purposes, exactly like the current Dutch net-metering scheme.
- **`net_metering=False`** (dynamic contract, the scheme phasing in from 2027): tax is levied on
  **gross consumption** — every kWh you import is taxed, even if you exported just as much
  somewhere else in the year.

The purchase price, supplier mark-ups, and feed-in revenue are calculated identically either
way; only the tax base changes.

**The web app always builds its `Provider` with `net_metering=False`** (`app.py`) — it only ever
models the post-salderingsregeling, dynamic-contract world described in the landing page text,
even though `Provider` itself supports both regimes. There's currently no UI toggle to evaluate
the net-metering scenario. `Provider` also accepts a `selling_fee_net_metering` flag, but it is
not read anywhere in the cost formulas yet — it's reserved for a possible future
net-metering-specific selling-fee rule.

## How "savings" are computed

The baseline bill uses the meter data exactly as recorded (no battery): `adjusted_consumption`
and `adjusted_production` are derived directly from `teruglevering − verbruik`, with no battery
in the loop. Each simulated bill uses the same formulas but with the battery-adjusted
`adjusted_consumption`/`adjusted_production` columns that `Simulator.run()` produces (see
[Optimization & control](optimization-and-control.md)). Savings are simply
`baseline_bill − simulated_bill`.

## Where the constants live

`energy_providers.py` hardcodes 2025 tariffs and a small set of DSOs (Enexis, Liander, Stedin,
Westland Infra, Coteq, RENDO). `get_energy_tax_excl_vat()` and `get_tax_discount()` raise a
`ValueError` for any year other than 2025 — there's no multi-year tariff table yet. The web app
never lets the user pick a DSO, so `BillingEngine`'s default (`dso='Enexis'`) is what every
simulation actually uses. To add a new year or DSO, extend the constant-returning functions in
`energy_providers.py`.
