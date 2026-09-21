from datetime import datetime, timezone

from app.sec_fundamentals import normalize_company_facts


RETRIEVED_AT = datetime(2026, 9, 20, 12, tzinfo=timezone.utc)


def fact(
    value,
    *,
    filed="2026-08-05",
    start="2026-03-29",
    end="2026-06-27",
    form="10-Q",
    accession="0000002488-26-000123",
    fiscal_year=2026,
    fiscal_period="Q2",
):
    return {
        "start": start,
        "end": end,
        "val": value,
        "accn": accession,
        "fy": fiscal_year,
        "fp": fiscal_period,
        "form": form,
        "filed": filed,
    }


def test_company_facts_preserve_numeric_value_period_and_provenance() -> None:
    payload = {
        "facts": {
            "us-gaap": {
                "RevenueFromContractWithCustomerExcludingAssessedTax": {
                    "units": {
                        "USD": [
                            fact(7_685_000_000),
                            fact(
                                6_800_000_000,
                                filed="2026-05-06",
                                start="2025-12-28",
                                end="2026-03-28",
                                accession="0000002488-26-000080",
                                fiscal_period="Q1",
                            ),
                        ]
                    }
                },
                "Assets": {
                    "units": {
                        "USD": [
                            fact(
                                76_000_000_000,
                                start=None,
                            )
                        ]
                    }
                },
            }
        }
    }

    facts = normalize_company_facts(
        ticker="AMD",
        cik="0000002488",
        payload=payload,
        retrieved_at=RETRIEVED_AT,
    )

    revenue = next(item for item in facts if item.metric == "revenue")
    assets = next(item for item in facts if item.metric == "assets")

    assert revenue.value == 7_685_000_000
    assert revenue.unit == "USD"
    assert revenue.period_start.isoformat() == "2026-03-29"
    assert revenue.period_end.isoformat() == "2026-06-27"
    assert revenue.fiscal_period == "Q2"
    assert revenue.available_at.isoformat() == "2026-08-06T00:00:00+00:00"
    assert revenue.source_url.endswith(
        "/2488/000000248826000123"
    )
    assert assets.period_start is None


def test_company_facts_exclude_values_not_available_by_retrieval_time() -> None:
    payload = {
        "facts": {
            "us-gaap": {
                "NetIncomeLoss": {
                    "units": {
                        "USD": [
                            fact(
                                1_000_000_000,
                                filed="2026-09-20",
                            )
                        ]
                    }
                }
            }
        }
    }

    facts = normalize_company_facts(
        ticker="AMD",
        cik="0000002488",
        payload=payload,
        retrieved_at=RETRIEVED_AT,
    )

    assert facts == []


def test_company_facts_support_ifrs_foreign_issuers() -> None:
    payload = {
        "facts": {
            "ifrs-full": {
                "Revenue": {
                    "units": {
                        "USD": [
                            fact(
                                2_500_000_000,
                                form="20-F",
                                accession="0001594805-26-000001",
                                fiscal_period="FY",
                            )
                        ]
                    }
                },
                "ProfitLoss": {
                    "units": {
                        "USD": [
                            fact(
                                300_000_000,
                                form="20-F",
                                accession="0001594805-26-000001",
                                fiscal_period="FY",
                            )
                        ]
                    }
                },
            }
        }
    }

    facts = normalize_company_facts(
        ticker="SHOP",
        cik="0001594805",
        payload=payload,
        retrieved_at=RETRIEVED_AT,
    )

    assert {item.metric for item in facts} == {"revenue", "net_income"}
    assert all(item.taxonomy == "ifrs-full" for item in facts)
    assert all(item.form == "20-F" for item in facts)
