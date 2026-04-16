# Refactoring Proposal: BattWatt Core Architecture

## 1. Current State & Pain Points
Currently, the project logic is spread across several script-like files. The simulation loop is duplicated in `example.py`, `tests/test_e2e.py`, and `tests/test_pv_controller.py`.

- **Duplicated Logic:** The grid interaction formula `net_grid_energy = (prod - cons) - (to_bat - from_bat) + (to_grid - from_grid)` is repeated manually.
- **Tight Coupling:** The financial calculations in `energy_providers.py` are mixed with the data structures of the simulation.
- **Inconsistent Interfaces:** Controllers have slightly different initialization requirements, making them hard to swap dynamically without manual code changes in the main loop.

## 2. Proposed Changes

### A. Unified Simulation Engine (`simulator.py`)
Create a `Simulator` class that encapsulates the execution logic.
```python
class Simulator:
    def __init__(self, battery, controller):
        self.battery = battery
        self.controller = controller

    def run(self, df: pd.DataFrame, duration_hours=0.25) -> SimulationResult:
        # Standardized loop logic here
        pass
```

### B. Structured Results (`models.py`)
Introduce a `SimulationResult` dataclass to hold the output DataFrame and pre-calculated totals. This replaces the practice of passing around raw DataFrames and manually summing columns.

### C. Decoupled Billing Engine
Refactor `energy_providers.py` to separate **Configuration** (Provider specs) from **Calculation** (The logic that applies those specs to a result).
- `Provider`: Data class for fees and flags.
- `BillingEngine`: Logic class that takes a `Provider` and a `SimulationResult` to produce a `Bill`.

### D. Controller Interface
Formalize the `BaseController` to ensure all future strategies (AI-based, Rule-based, etc.) are 100% interchangeable.

## 3. Benefits
- **Testability:** We can unit-test the `Simulator` independently of the data loading or plotting.
- **Maintainability:** Fixes to the grid flow logic only need to happen in one place (`simulator.py`).
- **Scalability:** New providers or battery types can be added via simple configuration instead of code changes.

## 4. Implementation Plan

1. **Phase 1: Simulator Extraction**
   - Move core loop to `simulator.py`.
   - Update `example.py` to use `Simulator.run()`.
2. **Phase 2: Result Object**
   - Implement `SimulationResult` to standardize the hand-off between simulation and financials.
3. **Phase 3: Billing Modularization**
   - Extract calculation logic from the `Provider` class.
4. **Phase 4: Test Alignment**
   - Refactor `tests/test_e2e.py` to use the unified engine, significantly reducing its line count.
