# BattWatt

BattWatt is a Python-based tool designed to evaluate and simulate household energy consumption, solar PV production, and battery storage performance using Dutch day-ahead energy prices. It allows users to estimate potential savings by optimizing battery usage based on price fluctuations and solar availability.

## Documentation

This README covers installation and day-to-day usage. For a deeper explanation of how the
numbers are actually produced, see:

- **[Cost calculation](docs/cost-calculation.md)** — the exact fixed/flexible cost formulas,
  VAT and energy tax handling, and the net-metering vs. gross-accounting distinction.
- **[Optimization & control](docs/optimization-and-control.md)** — how the PV, price-quantile,
  and MPC controllers each decide when to charge/discharge, including the MPC's full
  optimization formulation.
- **[Assumptions & known limitations](docs/assumptions-and-limitations.md)** — what the model
  simplifies or doesn't capture at all, including why **a battery can never "solve" PV
  curtailment** in this tool.
- **[CLAUDE.md](CLAUDE.md)** — architecture and module layout, for anyone extending the code.

## Features

- **Interactive Web App**: User-friendly Streamlit interface for easy data upload, multi-battery comparison, and simulation. Runs data in-memory only — nothing is stored (see the privacy notice in the app header).
- **Data Loading**:
  - Automatic format detection for smart meter (P1) exports: HomeWizard CSV, SlimmeMeterPortal.nl Excel, single-column "Kwartierdata" Excel, and standard DSO Excel exports.
  - Live import via the **SlimmeMeterPortal UserAPI** (no file upload needed).
  - A generic column-mapping loader for unsupported/custom CSV or Excel formats.
  - Built-in data quality checks (e.g. detecting HomeWizard CSVs corrupted by Excel round-tripping) surfaced as warnings before simulating.
  - Automated Day-Ahead price fetching via the **ENTSO-E API**, or manual import from ENTSO-E Excel exports.
- **Battery Simulation**: Realistic battery modeling including capacity, charge/discharge limits, and efficiency losses. Compare multiple battery configurations side by side in a single run.
- **Advanced Controllers**:
  - **PV Controller**: Maximizes self-consumption of solar energy.
  - **Price Controller**: Rule-based optimization using daily price quantiles (available at the simulation-engine/CLI level; not currently exposed in the web app strategy selector).
  - **MPC Controller**: Optimization-based (Model Predictive Control) using `Pyomo` and the `HiGHS` solver (via `highspy`) for cost-optimal behavior, with plan caching for performance.
- **Financial Analysis**: Comprehensive billing engine accounting for:
  - Dutch energy taxes and VAT (21%).
  - Net Metering (Salderingsregeling) — supported by the billing engine, not currently exposed in the web app (see [limitations](docs/assumptions-and-limitations.md)).
  - Custom provider fees (Subscription, Buying/Selling mark-ups).
  - Itemised cost-breakdown table comparing baseline vs. each simulated battery.
- **Visualization**: Interactive Plotly charts (energy flow, SoC, market price) and a visual progress bar for long-running simulations.

## Installation

### Prerequisites
- Python 3.12 or higher
- No separate HiGHS install is required — the solver ships as the `highspy` Python package in `requirements.txt`.

### Setup
1. Clone the repository.
2. Create and activate a virtual environment:
   ```powershell
   python -m venv .venv
   .\.venv\Scripts\Activate.ps1  # Windows
   ```
3. Install the required dependencies:
   ```powershell
   pip install -r requirements.txt
   ```

## Usage

### Web Application (Recommended)
Launch the interactive dashboard:
```powershell
streamlit run app.py
```
1. In the sidebar, choose a meter data source: upload a file (auto-detected format, or a custom column mapping) or fetch data live via the SlimmeMeterPortal API.
2. Add one or more battery configurations to compare (presets or custom capacity/power/efficiency).
3. Configure your energy provider's fees and choose a control strategy (PV self-consumption or MPC cost-optimal).
4. An ENTSO-E API key must be set in `.streamlit/secrets.toml` (as `ENTSOE_API_KEY`) for automated price fetching — the **🚀 Start Simulatie** button is disabled without it. A `SLIMMEMETERPORTAL_API_KEY` secret can also be set to pre-fill the SlimmeMeterPortal API key field.
5. Click **🚀 Start Simulatie** and review the results overview, cost breakdown, and interactive charts.

#### Failure alerts and the fix agent (optional)

When an upload, the ENTSO-E price fetch or the SlimmeMeterPortal API fails, the app triages the failure (`error_reporting.py`):

| Triage | Examples | Action |
|---|---|---|
| **fix** | unparseable values in a recognised format, crash in a known loader, unrecognised file with meter-like headers, unexpected API error/response | notify + open a GitHub issue for the agent |
| **outage** | timeout, connection error, HTTP 5xx, rate limit | notify only; escalated to **fix** if the same failure persists for 24 h |
| **config** | our ENTSO-E key rejected | notify only |
| **ignore** | random files, a user's own wrong API key, no price data for the period | nothing |

Reporting is isolated from the app: it runs on a background worker (the page never waits for ntfy or GitHub), and if the reporting module itself is broken the app simply runs without alerts.

Abuse limits (in memory, shared across sessions, expired entries are pruned): each file (content hash) and each distinct failure is reported once per 24 h, and each IP address at most 3 times per 24 h (only a salted hash of the IP is kept).

Alerts contain only the file's structure: extension, size, detected loader, sanitised column names, and masked value patterns (every digit becomes `9`, every letter `x`, e.g. `'-30` → `'-99`). Meter readings, timestamps, file names and IP addresses are never sent.

**Notifications.** Set `ALERT_WEBHOOK_URL`:
- **ntfy** (recommended): `"https://ntfy.sh/<long-random-topic>"`, then subscribe to that topic in the ntfy app. Topics are public to anyone who knows the name, so use a long random one.
- a **Discord** webhook URL, or a **Slack** incoming-webhook URL.

**Fix agent.** For **fix** failures the app opens an issue labelled `upload-failure` (one per distinct failure while it's open). `.github/workflows/upload-failure-agent.yml` then runs Claude, which first decides FIX / IGNORE / WAIT. IGNORE and WAIT close the issue with an explanation; FIX with a passing test suite opens a **draft PR** for review. At most 2 agent runs per day. Setup:
1. Streamlit secrets: `GITHUB_ISSUE_TOKEN` = a fine-grained personal access token for this repository only, with *Issues: read and write*. Optionally `GITHUB_REPO` (default `GreenYoghurt/BattWatt`).
2. Repository secret `CLAUDE_CODE_OAUTH_TOKEN` (from `claude setup-token`; uses your Claude subscription), and the Claude GitHub App installed on the repository.
3. Repository settings → Actions → General → enable *Allow GitHub Actions to create and approve pull requests*.
4. Create the `upload-failure` label (the workflow creates `agent-run` itself).

PRs opened by the workflow don't trigger CI automatically; marking the draft *Ready for review* does.

### CLI / Scripting
For developers, you can use the simulation engine directly:
```powershell
python example.py      # Rule-based simulation (Price controller)
python example_mpc.py  # Optimization-based simulation (MPC controller)
```

## Testing
Run the full test suite — covering physical energy conservation, financial E2E regressions, data-loader format detection, multi-battery comparison behavior, and the SlimmeMeterPortal API client:
```powershell
python -m pytest -s tests/
```

## License
MIT — see [LICENSE](LICENSE).
