"""Package backtest - Hệ thống kiểm thử chiến lược GOLD."""

from .data import (
    M1Data,
    attach_h1_atr,
    compute_wilder_atr,
    get_candle_counts_by_year,
    get_development_end_date,
    get_development_start_date,
    load_h1,
    load_m1,
    normalize_end_date,
    validate_date_range,
)

from .signals import (
    DEFAULT_MIN_ASIAN_BARS,
    compute_asian_range,
    generate_breakout_signals,
)

from .simulate import (
    DEFAULT_POINT,
    DEFAULT_SLIP_POINTS,
    DEFAULT_SPREAD_MULTIPLIER,
    STRESS_SPREAD_POINTS,
    YEARLY_MEDIAN_SPREADS,
    compute_yearly_spread_medians,
    get_effective_spread,
    simulate_single_trade,
    simulate_trades,
)

__all__ = [
    "M1Data",
    "load_m1",
    "load_h1",
    "compute_wilder_atr",
    "attach_h1_atr",
    "validate_date_range",
    "normalize_end_date",
    "get_development_end_date",
    "get_development_start_date",
    "get_candle_counts_by_year",
    "compute_asian_range",
    "generate_breakout_signals",
    "DEFAULT_MIN_ASIAN_BARS",
    "DEFAULT_POINT",
    "DEFAULT_SLIP_POINTS",
    "DEFAULT_SPREAD_MULTIPLIER",
    "STRESS_SPREAD_POINTS",
    "YEARLY_MEDIAN_SPREADS",
    "compute_yearly_spread_medians",
    "get_effective_spread",
    "simulate_single_trade",
    "simulate_trades",
]
