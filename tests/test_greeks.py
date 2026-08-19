"""
tests/test_greeks.py — Unit tests for local Black-Scholes Greek computation.

These tests run with no network calls.
"""

from __future__ import annotations

import pytest
from agent.options.greeks import compute_greeks, estimate_iv


class TestComputeGreeks:
    def test_call_delta_atm(self):
        """ATM call delta should be near 0.50."""
        g = compute_greeks("call", spot=100, strike=100, dte=30, iv=0.20)
        assert g
        assert 0.45 <= g["delta"] <= 0.60

    def test_put_delta_atm(self):
        """ATM put delta should be near -0.50."""
        g = compute_greeks("put", spot=100, strike=100, dte=30, iv=0.20)
        assert g
        assert -0.60 <= g["delta"] <= -0.40

    def test_call_put_delta_sum(self):
        """call.delta - |put.delta| ≈ 0 (put-call delta parity: Δcall + Δput ≈ 0 for same strike)."""
        call = compute_greeks("call", spot=100, strike=100, dte=30, iv=0.20)
        put = compute_greeks("put", spot=100, strike=100, dte=30, iv=0.20)
        assert call and put
        # Δcall + Δput should be close to 0 for ATM; allow up to 0.10 for drift
        assert abs(call["delta"] + put["delta"]) < 0.10

    def test_deep_itm_call_delta(self):
        """Deep ITM call delta should be close to 1.0."""
        g = compute_greeks("call", spot=120, strike=100, dte=30, iv=0.20)
        assert g
        assert g["delta"] > 0.85

    def test_deep_otm_put_delta(self):
        """Deep OTM put delta should be close to 0.0 (negative but small)."""
        g = compute_greeks("put", spot=100, strike=80, dte=30, iv=0.20)
        assert g
        assert -0.15 <= g["delta"] <= 0.0

    def test_gamma_positive(self):
        """Gamma is always positive."""
        for option_type in ("call", "put"):
            g = compute_greeks(option_type, spot=100, strike=100, dte=30, iv=0.20)
            assert g
            assert g["gamma"] > 0

    def test_theta_negative(self):
        """Theta is always negative (time decay)."""
        for option_type in ("call", "put"):
            g = compute_greeks(option_type, spot=100, strike=100, dte=30, iv=0.20)
            assert g
            assert g["theta"] < 0

    def test_vega_positive(self):
        """Vega is always positive."""
        for option_type in ("call", "put"):
            g = compute_greeks(option_type, spot=100, strike=100, dte=30, iv=0.20)
            assert g
            assert g["vega"] > 0

    def test_invalid_inputs_return_empty(self):
        """Invalid inputs should return empty dict, not crash."""
        assert compute_greeks("call", spot=0, strike=100, dte=30, iv=0.20) == {}
        assert compute_greeks("call", spot=100, strike=0, dte=30, iv=0.20) == {}
        assert compute_greeks("call", spot=100, strike=100, dte=0, iv=0.20) == {}
        assert compute_greeks("call", spot=100, strike=100, dte=30, iv=0.0) == {}

    def test_higher_iv_higher_vega(self):
        """Higher IV → higher vega."""
        g_low = compute_greeks("call", spot=100, strike=100, dte=30, iv=0.15)
        g_high = compute_greeks("call", spot=100, strike=100, dte=30, iv=0.35)
        assert g_low and g_high
        assert g_high["vega"] > g_low["vega"]


class TestEstimateIV:
    def test_roundtrip(self):
        """Compute a BS price, then back-solve IV — should recover original IV."""
        import math
        from agent.options.greeks import _d1_d2, _norm_cdf, RISK_FREE_RATE

        spot, strike, dte, iv = 100.0, 100.0, 30, 0.22
        t = dte / 365.0
        d1, d2 = _d1_d2(spot, strike, t, iv)
        bs_price = spot * _norm_cdf(d1) - strike * math.exp(-RISK_FREE_RATE * t) * _norm_cdf(d2)

        recovered = estimate_iv("call", spot, strike, dte, bs_price)
        assert recovered is not None
        assert abs(recovered - iv) < 0.005  # within 0.5% IV

    def test_invalid_inputs(self):
        assert estimate_iv("call", 100, 100, 0, 5.0) is None
        assert estimate_iv("call", 100, 100, 30, 0.0) is None
