"""Issue #47: Liander is the default grid operator (netbeheerder)."""
import pytest

from billing import BillingEngine
from energy_providers import DEFAULT_DSO, get_grid_operator_fee, get_providers


def test_default_dso_is_liander():
    assert DEFAULT_DSO == "Liander"
    assert get_grid_operator_fee() == pytest.approx(455.73)  # 2025, €1.2486/day incl. VAT


def test_billing_and_fixed_costs_use_the_default_dso():
    provider = get_providers()["Zonneplan"]
    assert BillingEngine(provider).dso == "Liander"
    expected = provider.subscription_cost + 455.73 - 635.19  # subscription + Liander - tax rebate
    assert provider.get_fixed_costs() == pytest.approx(expected)


def test_other_dsos_still_available():
    assert get_grid_operator_fee("Enexis") == pytest.approx(475.92)
