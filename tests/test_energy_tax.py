import pytest

from energy_providers import (
    VAT_RATE,
    get_energy_tax_excl_vat,
    get_energy_tax_incl_vat,
    tariff_incl_energy_tax,
)


def test_energy_tax_incl_vat():
    assert get_energy_tax_incl_vat() == pytest.approx(get_energy_tax_excl_vat() * (1 + VAT_RATE))


def test_tariff_quoted_excl_energy_tax_becomes_all_in():
    # Vandebron-style tariff card: delivery price incl. VAT, energy tax listed separately.
    eb = get_energy_tax_incl_vat()
    assert tariff_incl_energy_tax(0.2118) == pytest.approx(0.2118 + eb)
    assert tariff_incl_energy_tax(0.0) == pytest.approx(eb)
