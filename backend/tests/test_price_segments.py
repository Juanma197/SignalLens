from datetime import datetime, timezone

import pandas as pd

from app.price_segments import crosses_boundary, detect_price_segments
from app.research_scoring import walk_forward_evidence


def _prices(symbol="CASE.US", *, jump_at=150, raw_factor=30, adjusted_factor=30,
            low=False, zero_volume=False):
    dates = pd.bdate_range("2024-01-02", periods=310)
    base = .005 if low else 10.0
    raw, adjusted = [], []
    for index in range(len(dates)):
        continuous = base * (1.0002 ** index)
        raw.append(continuous * (raw_factor if index >= jump_at else 1))
        adjusted.append(continuous * (adjusted_factor if index >= jump_at else 1))
    return pd.DataFrame({"qualified_symbol": symbol, "trading_date": dates,
        "close": raw, "adjusted_close": adjusted,
        "volume": [0 if zero_volume and i in (jump_at - 1, jump_at) else 1000 for i in range(len(dates))],
        "status": "available", "source": "offline-fixture",
        "retrieved_at": pd.Timestamp("2026-01-01T00:00:00Z")})


def _observations(symbol="CASE.US"):
    return pd.DataFrame([{"security_id": symbol, "qualified_symbol": symbol,
        "region": "US", "currency": "USD", "eligible": True,
        "decision_at": datetime(2026, 1, 2, tzinfo=timezone.utc)}])


def test_atpc_like_zero_volume_amplification_creates_provenanced_boundary():
    prices = _prices(jump_at=150, raw_factor=25_000, adjusted_factor=25_000,
                     low=True, zero_volume=True)
    boundaries = detect_price_segments(prices)
    assert len(boundaries) == 1
    assert {"adjusted_price_discontinuity", "zero_volume_discontinuity",
            "missing_action_evidence"} <= set(boundaries.iloc[0].reason_codes)
    assert boundaries.iloc[0].provenance["left_source"] == "offline-fixture"


def test_split_like_and_raw_adjusted_disagreement_are_unresolved():
    split = detect_price_segments(_prices(raw_factor=560, adjusted_factor=22))
    disagree = detect_price_segments(_prices(raw_factor=293, adjusted_factor=24))
    assert "raw_adjusted_disagreement" in split.iloc[0].reason_codes
    assert "raw_adjusted_disagreement" in disagree.iloc[0].reason_codes


def test_boundaries_are_window_aware_and_clean_later_window_is_eligible():
    prices = _prices(jump_at=40)
    boundary = detect_price_segments(prices)
    dates = prices.trading_date
    assert crosses_boundary(boundary, "CASE.US", dates[20], dates[60])
    assert not crosses_boundary(boundary, "CASE.US", dates[100], dates[200])
    predictions, diagnostics = walk_forward_evidence(_observations(), prices,
        decision_at=datetime(2026, 1, 2, tzinfo=timezone.utc))
    assert len(predictions) > 0
    assert (predictions.vintage_date > dates[40]).all()
    assert diagnostics["diagnostics"]["segment_feature_rows_withheld"] > 0


def test_legitimate_continuous_low_price_remains_eligible():
    prices = _prices(low=True, raw_factor=1, adjusted_factor=1)
    assert detect_price_segments(prices).empty
    predictions, metrics = walk_forward_evidence(_observations(), prices,
        decision_at=datetime(2026, 1, 2, tzinfo=timezone.utc))
    assert len(predictions) > 0 and metrics["valid"]
    assert metrics["diagnostics"]["label_integrity"]["near_zero_denominators"] > 0
