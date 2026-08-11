"""
Unit tests for Battery's standby power draw (battery.py).

Covers the priority order the standby load is covered in (PV -> battery ->
grid), the defaults exposed by Battery/get_battery, and that the drawn
energy is properly accounted for as a conservation-visible loss.
"""
import pytest
from battery import Battery, get_battery

STANDBY_W = 10.0
DURATION_HOURS = 0.25
STANDBY_ENERGY_KWH = STANDBY_W / 1000 * DURATION_HOURS  # 0.0025 kWh


def test_standby_drawn_from_grid_when_battery_empty():
    """With no PV and an empty battery, the standby draw must spill to the grid."""
    bat = Battery(capacity_kwh=10, max_charge_kw=3.68, max_discharge_kw=3.68,
                  efficiency=1.0, standby_power_w=STANDBY_W)

    to_grid, from_grid = bat.step(0.0, 0.0, duration_hours=DURATION_HOURS)

    assert to_grid == pytest.approx(0.0)
    assert from_grid == pytest.approx(STANDBY_ENERGY_KWH)
    assert bat.soc_kwh == pytest.approx(0.0)


def test_standby_covered_by_battery_before_grid():
    """A battery with charge available must cover its own standby draw itself."""
    bat = Battery(capacity_kwh=10, max_charge_kw=3.68, max_discharge_kw=3.68,
                  efficiency=1.0, standby_power_w=STANDBY_W)
    bat.soc_kwh = 5.0

    to_grid, from_grid = bat.step(0.0, 0.0, duration_hours=DURATION_HOURS)

    assert to_grid == pytest.approx(0.0)
    assert from_grid == pytest.approx(0.0)
    assert bat.soc_kwh == pytest.approx(5.0 - STANDBY_ENERGY_KWH)


def test_standby_covered_by_pv_surplus_before_battery():
    """Excess PV production must absorb the standby draw before anything else happens."""
    bat = Battery(capacity_kwh=10, max_charge_kw=5.0, max_discharge_kw=5.0,
                  efficiency=1.0, standby_power_w=STANDBY_W)

    to_grid, from_grid = bat.step(1.0, 0.0, duration_hours=DURATION_HOURS)

    # All 1.0 kWh of PV minus the standby draw goes into the battery; nothing
    # is exported and nothing is imported.
    assert to_grid == pytest.approx(0.0)
    assert from_grid == pytest.approx(0.0)
    assert bat.soc_kwh == pytest.approx(1.0 - STANDBY_ENERGY_KWH)


def test_battery_default_standby_power_is_10w():
    bat = Battery(capacity_kwh=10, max_charge_kw=3.68, max_discharge_kw=3.68)
    assert bat.standby_power_w == 10.0


def test_get_battery_presets_default_to_10w_standby():
    for name in ["Bliq_5kwh", "Bliq_10kwh", "Bliq_10kwh_fast", "Bliq_15kwh"]:
        assert get_battery(name).standby_power_w == 10.0


def test_get_battery_standby_power_override():
    bat = get_battery("Bliq_5kwh", standby_power_w=0.0)
    assert bat.standby_power_w == 0.0


def test_standby_draw_is_conservation_visible_loss():
    """Over repeated idle intervals with no grid interaction, the SoC drop
    must exactly equal the total standby energy drawn (energy leaving the
    battery boundary, matching the conservation accounting used elsewhere)."""
    n_intervals = 20
    bat = Battery(capacity_kwh=10, max_charge_kw=3.68, max_discharge_kw=3.68,
                  efficiency=1.0, standby_power_w=STANDBY_W)
    bat.soc_kwh = 5.0  # enough charge that the battery never runs dry
    initial_soc = bat.soc_kwh

    grid_prod_sum = 0.0
    grid_cons_sum = 0.0
    for _ in range(n_intervals):
        to_grid, from_grid = bat.step(0.0, 0.0, duration_hours=DURATION_HOURS)
        grid_prod_sum += to_grid
        grid_cons_sum += from_grid

    delta_soc = bat.soc_kwh - initial_soc
    losses = 0 - (grid_prod_sum - grid_cons_sum) - delta_soc

    assert grid_prod_sum == pytest.approx(0.0)
    assert grid_cons_sum == pytest.approx(0.0)
    assert losses == pytest.approx(n_intervals * STANDBY_ENERGY_KWH)
