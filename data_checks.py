from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Optional
import pandas as pd


@dataclass
class CheckResult:
    passed: bool
    severity: str  # "warning" or "error"
    title: str
    message: str
    n_violations: int = 0


class DataCheck(ABC):
    @abstractmethod
    def run(self, raw_df: pd.DataFrame) -> Optional[CheckResult]:
        """Return None if this check is not applicable to the given data."""
        pass


class MaxPhaseCurrentCheck(DataCheck):
    """
    Flags rows where the computed interval consumption implausibly exceeds the
    phase power limit recorded in the previous interval. The most common cause is
    a HomeWizard CSV opened and re-saved in Microsoft Excel, which truncates decimal
    places from cumulative meter readings so that the .diff() step produces large
    integer values instead of small fractions.

    Two subtleties in the implementation:
    - L1/L2/L3 at row N reflect the power at the END of interval N (= start of
      interval N+1), so verbruik[N] must be compared against the PREVIOUS row's
      phase values (shift by 1).
    - Negative phase values indicate solar export on that phase and must be
      clamped to zero before summing, or they would unfairly shrink the limit.
    - A 3x ratio threshold filters out minor measurement discrepancies (max on
      clean data ≈ 2.74x) while catching real Excel corruption (ratio ≥ 5x).
    """

    PHASE_COLUMNS = ["L1 max W", "L2 max W", "L3 max W"]
    RATIO_THRESHOLD = 3.0
    MIN_VIOLATION_PCT = 50.0  # below this fraction of intervals, don't warn (likely isolated noise)

    def run(self, raw_df: pd.DataFrame) -> Optional[CheckResult]:
        available = [c for c in self.PHASE_COLUMNS if c in raw_df.columns]
        if not available or "verbruik" not in raw_df.columns or "timestamp" not in raw_df.columns:
            return None

        diffs = pd.to_datetime(raw_df["timestamp"]).diff().dropna()
        if diffs.empty:
            return None
        duration_hours = diffs.mode()[0].total_seconds() / 3600
        if duration_hours <= 0:
            return None

        # Use the previous row's phase power (start of the current interval).
        # Clip negatives (solar export) before summing so they don't reduce the limit.
        max_power_w = (
            raw_df[available].clip(lower=0).sum(axis=1).shift(1).bfill()
        )
        max_possible_kwh = max_power_w / 1000.0 * duration_hours

        # Only flag egregious exceedances to avoid measurement noise false positives.
        ratio = raw_df["verbruik"] / max_possible_kwh.replace(0, float("nan"))
        violations = (ratio > self.RATIO_THRESHOLD).fillna(False)
        n = int(violations.sum())
        pct = n / len(raw_df) * 100 if len(raw_df) > 0 else 0.0

        if pct < self.MIN_VIOLATION_PCT:
            return CheckResult(
                passed=True,
                severity="warning",
                title="Verbruik vs. vermogenslimiet",
                message="Alle verbruikswaarden liggen binnen de gemeten vermogenslimieten (L1/L2/L3).",
            )

        return CheckResult(
            passed=False,
            severity="warning",
            title="Mogelijk bestandscorruptie gedetecteerd",
            message=(
                f"{n} van {len(raw_df)} intervallen ({pct:.1f}%) hebben een verbruikswaarde "
                f"die hoger is dan fysiek mogelijk op basis van de L1/L2/L3 piekvermogens. "
                f"Dit wijst waarschijnlijk op een CSV-bestand dat via Microsoft Excel is geopend "
                f"en opgeslagen, waardoor decimalen in de cumulatieve meterwaarden verloren zijn gegaan. "
                f"Gebruik het originele bestand direct vanuit de HomeWizard-app."
            ),
            n_violations=n,
        )


_ALL_CHECKS: list[DataCheck] = [MaxPhaseCurrentCheck()]


def run_checks(raw_df: pd.DataFrame) -> list[CheckResult]:
    """Run all registered checks on the raw (pre-validate) meter DataFrame."""
    results = []
    for check in _ALL_CHECKS:
        result = check.run(raw_df)
        if result is not None:
            results.append(result)
    return results
