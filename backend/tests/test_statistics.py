from app.statistics import safe_correlation


def test_safe_correlation_requires_minimum_and_finite_variance():
    assert safe_correlation([1, 2], [2, 1])["reason"] == "insufficient_observations"
    assert safe_correlation([1, 2, 3], [3, 2, 1])["value"] == -1.0
