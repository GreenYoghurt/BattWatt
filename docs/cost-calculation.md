# How costs are calculated

JortApp estimates a full-year electricity bill twice — once for the situation as measured
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
| Netbeheerskosten (grid operator fee) | `get_grid_operator_fee(dso)` | €455.73/year (Liander, the default) |
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
2. **Energiebelasting** (energy tax) — a flat €0.10154/kWh excl. VAT (2025, the same rate for
   brackets 0–2,900 and 2,901–10,000 kWh), so €0.1229/kWh incl. VAT as it appears on a 2025
   energy bill. VAT is added on top. **This is where net metering (salderingsregeling) changes the outcome** — see
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

## Vast contract (fixed contract)

`Provider.contract_type` can be `"dynamic"` (everything above) or `"fixed"`. Under a fixed
contract the flexible costs are computed by `Provider.calculate_fixed_costs_breakdown()` instead
of `calculate_flexible_costs_breakdown()` — `Provider.calculate_costs_breakdown()` /
`calculate_costs_total()` dispatch to the right one based on `contract_type`, so `billing.py`
never branches on it itself.

- **Normaaltarief / daltarief** — every 15-minute interval of consumption is priced at
  `Provider.normaal_tarief` or `Provider.dal_tarief` depending on when it falls, via
  `get_tariff_period(dt)`:
  - **Dal**: weekdays 23:00–07:00, plus the entire weekend (Saturday and Sunday, all day).
  - **Normaal**: weekdays 07:00–23:00.
- **Terugleververgoeding** — every exported kWh is paid at a single flat
  `Provider.terugleververgoeding` rate, regardless of when it was exported. There is **no net
  metering (saldering)** for fixed contracts in this tool — feed-in never offsets the
  normaal/dal consumption total, it's only ever paid out separately.
- **Tariffs are all-in**: `normaal_tarief`, `dal_tarief`, and `terugleververgoeding` are entered
  VAT-inclusive and already include energiebelasting. Unlike the dynamic contract, there is no
  separate `energiebelasting` line in the fixed-contract cost breakdown — it's baked into the two
  per-kWh rates.
- **Required: incl. or excl. energiebelasting.** Many suppliers (e.g. Vandebron) quote the
  normaal/dal delivery price incl. VAT but list energiebelasting as a separate line, so entering
  those prices directly would leave out ~€0.13/kWh. The app therefore requires the user to state,
  per fixed contract, whether the entered normaal/dal tariffs include energiebelasting (no
  default; "Start Simulatie" stays disabled until chosen). For "exclusief",
  `energy_providers.tariff_incl_energy_tax()` adds `get_energy_tax_incl_vat()` to the normaal and
  dal tariffs before they reach `Provider`. The terugleververgoeding is never adjusted: no
  energiebelasting is levied on feed-in.

```
flexible_costs = normaal_kwh * normaal_tarief + dal_kwh * dal_tarief
                − total_feed_in_kwh * terugleververgoeding

total_bill = fixed_costs + flexible_costs
```

Fixed annual costs (abonnementskosten, netbeheerskosten, belastingvermindering) are computed
identically for both contract types — `Provider.get_fixed_costs()` is contract-type-agnostic.

Because "Kosten Optimaal (MPC)" optimizes battery charge/discharge against the dynamic day-ahead
price, that price signal no longer reflects what the customer actually pays under a fixed
contract. `app.py` therefore only offers "PV Prioriteit" as a strategy when "Vast contract" is
selected.

## How "savings" are computed

The baseline bill uses the meter data exactly as recorded (no battery): `adjusted_consumption`
and `adjusted_production` are derived directly from `teruglevering − verbruik`, with no battery
in the loop. Each simulated bill uses the same formulas but with the battery-adjusted
`adjusted_consumption`/`adjusted_production` columns that `Simulator.run()` produces (see
[Optimization & control](optimization-and-control.md)). Savings are simply
`baseline_bill − simulated_bill`.

## Comparing energy providers

In the sidebar's "💶 Energieleverancier" section you can add more than one contract
("➕ Leverancier toevoegen"), each with its own name, contract type (dynamic or fixed), fixed
delivery costs per year, and either the supplier mark-ups (dynamic) or the normaal-/daltarief and
terugleververgoeding (fixed). Every provider is billed with exactly the formulas above.

**The battery is simulated once, not once per provider.** The baseline and every battery
simulation produce one `SimulationResult` each; `billing.compare_providers()` then bills those same
results under every provider (a `BillingEngine` per provider). What the battery does therefore
does not depend on which provider you look at:

- **PV Prioriteit** never looks at prices or fees, so its dispatch is the same for every contract
  anyway.
- **Kosten Optimaal (MPC)** does use the provider: `Controller_MPC` adds `buying_fee`,
  `selling_fee` and the energy tax to the day-ahead price in its objective. With several
  providers, **Leverancier 1 (the first one in the list) drives the MPC dispatch**, and the other
  providers are billed on that same dispatch. For a provider with different mark-ups an
  optimizer tuned to that provider could do slightly better, so the comparison slightly favours
  Leverancier 1 under MPC. Leverancier 1 also decides which strategies are offered: if it is a
  fixed contract, only PV Prioriteit is available.

Per provider, `compare_providers()` returns the bill without battery, the bill with each battery,
and `savings = bill_without_battery − bill_with_battery` **within that provider**. All amounts
come both including and excluding the fixed annual costs; the savings are the same either way,
because one provider's fixed costs are identical with and without a battery.

In the app, Leverancier 1 is still the one shown in the existing overview and Kostenopbouw (so
with a single provider nothing changes). With two or more providers an extra "Vergelijking
energieleveranciers" section appears:

- **Handmatige batterijen:** grouped bar charts of yearly cost (without battery and with each
  battery) and of savings per provider, plus a table with the same numbers (CSV download).
- **Batterijgrootte vergelijking:** the existing size charts stay for Leverancier 1; the comparison
  section adds one line per provider for yearly cost (0 kWh = no battery) and savings versus
  battery size at 90% efficiency, plus a table with every provider × size × efficiency
  combination (CSV download).

Both also name the cheapest provider/battery combination. The "Vaste kosten meenemen" toggle
applies to the comparison too.

## Where the constants live

`energy_providers.py` hardcodes 2025 tariffs and a small set of DSOs (Enexis, Liander, Stedin,
Westland Infra, Coteq, RENDO). `get_energy_tax_excl_vat()` and `get_tax_discount()` raise a
`ValueError` for any year other than 2025 — there's no multi-year tariff table yet. The web app
never lets the user pick a DSO, so the default `DEFAULT_DSO = 'Liander'` (used by
`BillingEngine` and `Provider.get_fixed_costs()`) is what every simulation actually uses. To add a new year or DSO, extend the constant-returning functions in
`energy_providers.py`.
