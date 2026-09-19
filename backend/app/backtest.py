from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any

import pandas as pd
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import brier_score_loss, roc_auc_score
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import StandardScaler

from .research import FEATURE_COLUMNS


@dataclass(frozen=True)
class BacktestResult:
    predictions: pd.DataFrame
    summary: dict[str, Any]


def walk_forward_backtest(
    dataset: pd.DataFrame,
    min_training_months: int = 36,
    top_k: int = 3,
    transaction_cost_bps_per_side: float = 10.0,
) -> BacktestResult:
    """Run an expanding-window classification backtest.

    For each prediction date, a training row is eligible only when its exit date
    is earlier than the prediction date. This prevents outcomes that were not
    yet observable from leaking into model training.
    """
    if min_training_months < 12:
        raise ValueError("min_training_months must be at least 12")
    if top_k < 1:
        raise ValueError("top_k must be positive")
    if transaction_cost_bps_per_side < 0:
        raise ValueError("transaction costs cannot be negative")

    required = {
        "ticker",
        "as_of_date",
        "entry_date",
        "exit_date",
        "forward_return",
        "outperformed_universe",
        *FEATURE_COLUMNS,
    }
    missing = required - set(dataset.columns)
    if missing:
        raise ValueError(f"Missing backtest columns: {', '.join(sorted(missing))}")
    if dataset.empty:
        return BacktestResult(pd.DataFrame(), _empty_summary())

    frame = dataset.copy()
    for column in ("as_of_date", "entry_date", "exit_date"):
        frame[column] = pd.to_datetime(frame[column])
    frame = frame.sort_values(["as_of_date", "ticker"]).reset_index(drop=True)

    prediction_batches: list[pd.DataFrame] = []
    for prediction_date in sorted(frame["as_of_date"].unique()):
        training = frame.loc[frame["exit_date"] < prediction_date]
        training_months = training["as_of_date"].nunique()
        if training_months < min_training_months:
            continue
        if training["outperformed_universe"].nunique() < 2:
            continue

        test = frame.loc[frame["as_of_date"] == prediction_date].copy()
        if test.empty:
            continue

        model = Pipeline(
            [
                ("scale", StandardScaler()),
                (
                    "model",
                    LogisticRegression(
                        C=1.0,
                        max_iter=2_000,
                        random_state=0,
                    ),
                ),
            ]
        )
        model.fit(
            training.loc[:, FEATURE_COLUMNS],
            training["outperformed_universe"],
        )

        test["predicted_probability"] = model.predict_proba(
            test.loc[:, FEATURE_COLUMNS]
        )[:, 1]
        test["baseline_probability"] = float(
            training["outperformed_universe"].mean()
        )
        test["rank"] = (
            test["predicted_probability"]
            .rank(method="first", ascending=False)
            .astype(int)
        )
        test["training_rows"] = len(training)
        test["training_months"] = training_months
        test["training_through_exit_date"] = training["exit_date"].max()
        prediction_batches.append(test)

    if not prediction_batches:
        return BacktestResult(pd.DataFrame(), _empty_summary())

    predictions = pd.concat(prediction_batches, ignore_index=True)
    cost = 2.0 * transaction_cost_bps_per_side / 10_000.0
    predictions["net_forward_return"] = predictions["forward_return"] - cost

    top_one = predictions.loc[predictions["rank"] == 1]
    top_group = predictions.loc[predictions["rank"] <= top_k]
    top_group_by_month = top_group.groupby("as_of_date")["net_forward_return"].mean()
    universe_by_month = predictions.groupby("as_of_date")["forward_return"].mean()
    aligned = pd.concat(
        [
            top_group_by_month.rename("selected"),
            universe_by_month.rename("universe"),
        ],
        axis=1,
    ).dropna()

    labels = predictions["outperformed_universe"]
    probabilities = predictions["predicted_probability"]
    baseline_probabilities = predictions["baseline_probability"]

    summary = {
        "prediction_rows": int(len(predictions)),
        "prediction_months": int(predictions["as_of_date"].nunique()),
        "first_prediction_date": predictions["as_of_date"].min(),
        "last_prediction_date": predictions["as_of_date"].max(),
        "brier_score": float(brier_score_loss(labels, probabilities)),
        "baseline_brier_score": float(
            brier_score_loss(labels, baseline_probabilities)
        ),
        "roc_auc": float(roc_auc_score(labels, probabilities))
        if labels.nunique() == 2
        else None,
        "top_one_mean_net_return": float(top_one["net_forward_return"].mean()),
        "top_k": int(top_k),
        "top_k_mean_net_return": float(top_group_by_month.mean()),
        "universe_mean_return": float(universe_by_month.mean()),
        "top_k_mean_excess_return": float(
            (aligned["selected"] - aligned["universe"]).mean()
        ),
        "top_k_monthly_win_rate": float(
            (aligned["selected"] > aligned["universe"]).mean()
        ),
        "transaction_cost_bps_per_side": float(
            transaction_cost_bps_per_side
        ),
    }

    output_columns = [
        "ticker",
        "as_of_date",
        "entry_date",
        "exit_date",
        "predicted_probability",
        "baseline_probability",
        "rank",
        "forward_return",
        "net_forward_return",
        "outperformed_universe",
        "training_rows",
        "training_months",
        "training_through_exit_date",
    ]
    return BacktestResult(predictions.loc[:, output_columns], summary)


def _empty_summary() -> dict[str, Any]:
    return {
        "prediction_rows": 0,
        "prediction_months": 0,
        "first_prediction_date": None,
        "last_prediction_date": None,
    }


def momentum_backtest(
    dataset: pd.DataFrame,
    momentum_feature: str = "momentum_126d",
    top_k: int = 3,
    transaction_cost_bps_per_side: float = 10.0,
    evaluation_dates: Iterable[pd.Timestamp] | None = None,
) -> BacktestResult:
    """Evaluate a transparent cross-sectional momentum ranking benchmark."""
    if momentum_feature not in FEATURE_COLUMNS:
        raise ValueError(f"Unknown momentum feature: {momentum_feature}")
    if not momentum_feature.startswith("momentum_"):
        raise ValueError("momentum_feature must be a momentum feature")
    if top_k < 1:
        raise ValueError("top_k must be positive")
    if transaction_cost_bps_per_side < 0:
        raise ValueError("transaction costs cannot be negative")

    required = {
        "ticker",
        "as_of_date",
        "entry_date",
        "exit_date",
        "forward_return",
        momentum_feature,
    }
    missing = required - set(dataset.columns)
    if missing:
        raise ValueError(f"Missing benchmark columns: {', '.join(sorted(missing))}")
    if dataset.empty:
        return BacktestResult(pd.DataFrame(), _empty_summary())

    frame = dataset.copy()
    for column in ("as_of_date", "entry_date", "exit_date"):
        frame[column] = pd.to_datetime(frame[column])

    if evaluation_dates is not None:
        allowed_dates = pd.to_datetime(list(evaluation_dates))
        frame = frame.loc[frame["as_of_date"].isin(allowed_dates)].copy()
    if frame.empty:
        return BacktestResult(pd.DataFrame(), _empty_summary())

    frame["score"] = frame[momentum_feature]
    frame["rank"] = (
        frame.groupby("as_of_date")["score"]
        .rank(method="first", ascending=False)
        .astype(int)
    )
    cost = 2.0 * transaction_cost_bps_per_side / 10_000.0
    frame["net_forward_return"] = frame["forward_return"] - cost

    selected = frame.loc[frame["rank"] <= top_k]
    selected_by_month = selected.groupby("as_of_date")["net_forward_return"].mean()
    universe_by_month = frame.groupby("as_of_date")["forward_return"].mean()
    comparison = pd.concat(
        [
            selected_by_month.rename("selected"),
            universe_by_month.rename("universe"),
        ],
        axis=1,
    ).dropna()
    comparison["excess"] = comparison["selected"] - comparison["universe"]

    wealth = (1.0 + selected_by_month).cumprod()
    drawdown = wealth / wealth.cummax() - 1.0
    selection_counts = selected["ticker"].value_counts()
    selection_slots = max(int(len(selected)), 1)

    summary = {
        "strategy": momentum_feature,
        "prediction_rows": int(len(frame)),
        "prediction_months": int(frame["as_of_date"].nunique()),
        "first_prediction_date": frame["as_of_date"].min(),
        "last_prediction_date": frame["as_of_date"].max(),
        "top_k": int(top_k),
        "top_k_mean_net_return": float(selected_by_month.mean()),
        "top_k_median_net_return": float(selected_by_month.median()),
        "universe_mean_return": float(universe_by_month.mean()),
        "top_k_mean_excess_return": float(comparison["excess"].mean()),
        "top_k_median_excess_return": float(comparison["excess"].median()),
        "top_k_monthly_win_rate": float(
            (comparison["excess"] > 0).mean()
        ),
        "positive_return_rate": float((selected_by_month > 0).mean()),
        "maximum_drawdown": float(drawdown.min()),
        "worst_month": float(selected_by_month.min()),
        "best_month": float(selected_by_month.max()),
        "most_selected_ticker": str(selection_counts.index[0]),
        "most_selected_ticker_share": float(
            selection_counts.iloc[0] / selection_slots
        ),
        "transaction_cost_bps_per_side": float(
            transaction_cost_bps_per_side
        ),
    }

    output_columns = [
        "ticker",
        "as_of_date",
        "entry_date",
        "exit_date",
        "score",
        "rank",
        "forward_return",
        "net_forward_return",
    ]
    return BacktestResult(
        frame.loc[:, output_columns]
        .sort_values(["as_of_date", "rank"])
        .reset_index(drop=True),
        summary,
    )
