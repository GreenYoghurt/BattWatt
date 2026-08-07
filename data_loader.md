# Data Loading Strategy - BattWatt

> **Status: Mostly implemented.** `data_loader.py` now has a `MeterDataLoader` registry
> (`HomeWizardLoader`, `SlimmeMeterPortalLoader`, `SingleColumnLoader`, `StandardExcelLoader`)
> with `SmartLoader` doing format auto-detection, plus `GenericMappedLoader` for custom
> mappings and a `DataCheck` framework (`data_checks.py`) for validation warnings — see
> `CLAUDE.md`'s Architecture section for the current module layout. One deviation from the
> plan below: the generic mapping config is **JSON, not YAML** (see the corrected example in
> "User-Friendly Configuration" below). Not yet built: a live-fetch loader per platform beyond
> the SlimmeMeterPortal UserAPI (`slimmemeterportal_client.py` + `SlimmeMeterPortalAPILoader`),
> and no "sniff and suggest a mapping" fallback beyond the current auto-detect + explicit-error
> flow.

Currently, BattWatt supports specific formats from HomeWizard and a generic Excel format. To make the project more accessible to users with different energy monitoring systems (e.g., Enphase, SolarEdge, P1-monitors, or raw DSO exports), we need a flexible and user-friendly data loading strategy.

## Goal
Enable users to import their energy consumption data regardless of the source, with minimal configuration and high reliability.

## Proposed Architecture

### 1. Unified Interface
Every loader must return a standardized `pd.DataFrame` with the following columns:
- `timestamp`: (datetime64) The start of the measurement interval.
- `verbruik`: (float, kWh) Energy imported from the grid in that interval.
- `teruglevering`: (float, kWh) Energy exported to the grid in that interval.

### 2. Loader Types
We distinguish between three types of loaders:
- **Predefined Loaders**: High-quality, tested loaders for popular platforms (HomeWizard, Slimme Meter Excel, etc.).
- **Generic Configurable Loader**: A "Swiss Army Knife" loader where users define a simple mapping in a YAML or JSON file.

### 3. Automatic Format Detection
To improve user experience, the system should attempt to "sniff" the file:
1. Read the first few lines/headers.
2. Compare headers against a registry of known formats.
3. If a match is found, use the corresponding predefined loader.
4. If no match is found, fallback to the Generic Loader or ask the user for a mapping.

## User-Friendly Configuration (Schema Mapping)

If a user has a custom CSV, they shouldn't have to write Python code. They should be able to provide a simple mapping. **As implemented, this is a JSON config** (passed as a dict, or a path loaded with `json.load`) rather than the YAML originally sketched here:

```json
{
  "format": "csv",
  "delimiter": ";",
  "decimal": ",",
  "columns": {
    "timestamp": "Tijdstip",
    "import": "Totaal Verbruik (kWh)",
    "export": "Totaal Injectie (kWh)"
  },
  "is_cumulative": true
}
```

`columns` also supports a single signed `"value"` column (positive = consumption, negative =
production, as used by `SingleColumnLoader`) instead of separate `import`/`export` keys — see
`GenericMappedLoader.load()` in `data_loader.py`. The web app exposes this mapping via the
"Aangepaste Mapping" sidebar expander in `app.py`.

## Implementation Plan

### Phase 1: Refactor `data_loader.py`
- Introduce a `MeterDataLoader` base class or a registry pattern.
- Move existing logic into specialized functions/classes.
- Create a `SmartLoader` that handles the auto-detection.

### Phase 2: Generic Mapping Support
- Implement the ability to read a configuration file (like the YAML example above) to parse arbitrary CSV/Excel files.

### Phase 3: Validation & Error Handling
- Add a validation step that checks for:
    - Missing timestamps (gaps in data).
    - Overlapping data.
    - Physical impossibilities (e.g., negative consumption after diff).
- Provide clear, actionable error messages (e.g., "Column 'Tijdstip' not found. Available columns: ['Date', 'Power']").

## Example Usage (Target)

```python
from data_loader import SmartLoader

# Auto-detects if it's HomeWizard, Slimme Meter, or a custom mapped file
df = SmartLoader.load("my_data.csv")
```

---
*This strategy aligns with the goal of making BattWatt the go-to tool for Dutch home energy simulation by lowering the barrier to entry for non-developers.*
