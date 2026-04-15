# Project TODOs

## Bugs & Fixes
- [x] **Fix energy flow calculation in `example.py`**: 
  The current calculation for `production2` and `consumption2` can result in negative values when the battery charges from or discharges to the grid. 
  ```python
  # Current problematic logic:
  production2 = production - to_battery + to_grid
  consumption2 = consumption - from_battery + from_grid
  ```
  It should be refactored to calculate a single net grid flow and then split it into non-negative production and consumption values.
