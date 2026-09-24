"""Point-in-time global fundamentals and multifactor shadow research.

This module is deliberately disconnected from the production publisher.  It accepts
validated operator files, preserves raw facts, and writes only ``research_*`` tables.
"""
from __future__ import annotations

import csv
import hashlib
import io
import json
from dataclasses import asdict, dataclass
from datetime import date, datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import Iterable

import duckdb
import numpy as np
import pandas as pd
from sklearn.linear_model import Ridge

from .global_universe import utc_naive

FEATURE_VERSION = "global-multifactor-v1"
BASELINE_VERSION = "fixed-baseline-v1"
ALLOWED_STANDARDS = {"US-GAAP", "IFRS", "OTHER", "UNKNOWN"}
ALLOWED_STATES = {"available", "missing", "stale", "unsupported", "invalid"}
METRIC_ALIASES = {
    "revenues": "revenue", "revenue": "revenue", "salesrevenuenet": "revenue",
    "operatingincome": "operating_income", "operatingincomeloss": "operating_income",
    "netincome": "net_income", "netincomeloss": "net_income",
    "dilutedeps": "diluted_eps", "earningspersharediluted": "diluted_eps",
    "operatingcashflow": "operating_cash_flow", "netcashprovidedbyoperatingactivities": "operating_cash_flow",
    "capitalexpenditure": "capital_expenditure", "paymentstoacquirepropertyplantandequipment": "capital_expenditure",
    "cashandcashequivalents": "cash", "cashandcashequivalentsatcarryingvalue": "cash",
    "debt": "debt", "longtermdebtandfinanceliaseobligations": "debt",
    "assets": "assets", "liabilities": "liabilities", "stockholdersequity": "equity",
    "shareholdersequity": "equity", "sharesoutstanding": "shares_outstanding",
    "commonstocksharesoutstanding": "shares_outstanding", "interestexpense": "interest_expense",
}

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS research_filing_reports (
 report_id VARCHAR PRIMARY KEY, security_id VARCHAR NOT NULL, jurisdiction VARCHAR NOT NULL,
 accounting_standard VARCHAR NOT NULL, form_type VARCHAR, period_start DATE, period_end DATE NOT NULL,
 published_at TIMESTAMP NOT NULL, available_at TIMESTAMP NOT NULL, retrieved_at TIMESTAMP NOT NULL,
 source_document VARCHAR NOT NULL, source_provider VARCHAR NOT NULL, provenance_json VARCHAR NOT NULL,
 supersedes_report_id VARCHAR, is_restated BOOLEAN NOT NULL
);
CREATE TABLE IF NOT EXISTS research_raw_fundamental_facts (
 fact_id VARCHAR PRIMARY KEY, report_id VARCHAR NOT NULL, security_id VARCHAR NOT NULL,
 source_concept VARCHAR NOT NULL, value DECIMAL(38,10), currency VARCHAR, unit VARCHAR NOT NULL,
 period_start DATE, period_end DATE NOT NULL, published_at TIMESTAMP NOT NULL,
 available_at TIMESTAMP NOT NULL, retrieved_at TIMESTAMP NOT NULL, accounting_standard VARCHAR NOT NULL,
 source_document VARCHAR NOT NULL, source_provider VARCHAR NOT NULL, provenance_json VARCHAR NOT NULL,
 revision_of_fact_id VARCHAR, state VARCHAR NOT NULL
);
CREATE TABLE IF NOT EXISTS research_normalized_fundamentals (
 normalized_id VARCHAR PRIMARY KEY, fact_id VARCHAR NOT NULL, security_id VARCHAR NOT NULL,
 metric VARCHAR NOT NULL, value DECIMAL(38,10), currency VARCHAR, unit VARCHAR NOT NULL,
 period_start DATE, period_end DATE NOT NULL, available_at TIMESTAMP NOT NULL, retrieved_at TIMESTAMP NOT NULL,
 mapping_version VARCHAR NOT NULL, state VARCHAR NOT NULL, explanation VARCHAR NOT NULL
);
CREATE TABLE IF NOT EXISTS research_feature_sets (
 feature_set_id VARCHAR PRIMARY KEY, universe_snapshot_id VARCHAR NOT NULL, security_id VARCHAR NOT NULL,
 evaluated_at TIMESTAMP NOT NULL, feature_version VARCHAR NOT NULL, features_json VARCHAR NOT NULL,
 states_json VARCHAR NOT NULL, evidence_json VARCHAR NOT NULL, content_hash VARCHAR NOT NULL
);
CREATE TABLE IF NOT EXISTS research_shadow_vintages (
 vintage_id VARCHAR PRIMARY KEY, model_version VARCHAR NOT NULL, universe_snapshot_id VARCHAR NOT NULL,
 feature_version VARCHAR NOT NULL, evaluated_at TIMESTAMP NOT NULL, horizon_days INTEGER NOT NULL,
 methodology_json VARCHAR NOT NULL, content_hash VARCHAR NOT NULL, created_at TIMESTAMP NOT NULL
);
CREATE TABLE IF NOT EXISTS research_shadow_candidates (
 vintage_id VARCHAR NOT NULL, security_id VARCHAR NOT NULL, rank INTEGER, overall_score DOUBLE,
 confidence DOUBLE NOT NULL, eligible BOOLEAN NOT NULL, lens_scores_json VARCHAR NOT NULL,
 contributions_json VARCHAR NOT NULL, exclusions_json VARCHAR NOT NULL, evidence_json VARCHAR NOT NULL,
 PRIMARY KEY(vintage_id, security_id)
);
CREATE TABLE IF NOT EXISTS research_shadow_outcomes (
 vintage_id VARCHAR NOT NULL, security_id VARCHAR NOT NULL, horizon_end DATE NOT NULL,
 local_return DOUBLE, gbp_return DOUBLE, benchmark_return DOUBLE, evaluated_at TIMESTAMP NOT NULL,
 PRIMARY KEY(vintage_id, security_id, horizon_end)
);
CREATE TABLE IF NOT EXISTS research_evaluations (
 evaluation_id VARCHAR PRIMARY KEY, created_at TIMESTAMP NOT NULL, configuration_json VARCHAR NOT NULL,
 metrics_json VARCHAR NOT NULL, promotion_decision VARCHAR NOT NULL
);
"""

@dataclass(frozen=True)
class RawFundamentalFact:
    security_id: str; report_id: str; source_concept: str; value: Decimal | None
    currency: str | None; unit: str; period_start: date | None; period_end: date
    published_at: datetime; available_at: datetime; retrieved_at: datetime
    accounting_standard: str; source_document: str; source_provider: str
    provenance: dict; revision_of_fact_id: str | None = None; state: str = "available"

    def __post_init__(self) -> None:
        if self.accounting_standard not in ALLOWED_STANDARDS or self.state not in ALLOWED_STATES:
            raise ValueError("Unsupported accounting standard or fact state")
        if any(x.tzinfo is None for x in (self.published_at, self.available_at, self.retrieved_at)):
            raise ValueError("All fact timestamps must be timezone-aware")
        if self.available_at < self.published_at:
            raise ValueError("available_at cannot precede publication")
        if self.state == "available" and self.value is None:
            raise ValueError("Available facts require a value")

    @property
    def fact_id(self) -> str:
        raw = "|".join((self.report_id, self.security_id, self.source_concept, self.period_end.isoformat(),
                        self.available_at.isoformat(), str(self.value), self.unit))
        return hashlib.sha256(raw.encode()).hexdigest()

def parse_operator_facts(text: str, *, source_provider: str, retrieved_at: datetime) -> list[RawFundamentalFact]:
    """Validate the common operator CSV used for UK/Canada/Europe and optional US files."""
    reader = csv.DictReader(io.StringIO(text))
    required = {"security_id", "report_id", "source_concept", "value", "currency", "unit", "period_start",
                "period_end", "published_at", "available_at", "accounting_standard", "source_document"}
    missing = required - set(reader.fieldnames or ())
    if missing: raise ValueError(f"Missing fundamental columns: {', '.join(sorted(missing))}")
    rows = []
    for row in reader:
        dt = lambda key: datetime.fromisoformat(row[key].replace("Z", "+00:00"))
        rows.append(RawFundamentalFact(row["security_id"].strip(), row["report_id"].strip(),
            row["source_concept"].strip(), Decimal(row["value"]) if row["value"].strip() else None,
            row["currency"].strip().upper() or None, row["unit"].strip(),
            date.fromisoformat(row["period_start"]) if row["period_start"].strip() else None,
            date.fromisoformat(row["period_end"]), dt("published_at"), dt("available_at"), retrieved_at,
            row["accounting_standard"].strip().upper(), row["source_document"].strip(), source_provider,
            {"operator_file": True}, row.get("revision_of_fact_id") or None, (row.get("state") or "available").lower()))
    return rows

def normalize_metric(concept: str) -> str | None:
    local = concept.rsplit(":", 1)[-1]
    return METRIC_ALIASES.get("".join(ch for ch in local.lower() if ch.isalnum()))

def robust_peer_scores(frame: pd.DataFrame, value: str, *, higher_is_better: bool = True,
                       minimum_peers: int = 5) -> pd.Series:
    """Point-in-time winsorized percentile using an explicit peer fallback hierarchy."""
    result = pd.Series(np.nan, index=frame.index, dtype=float)
    levels = [["industry", "region", "accounting_standard"], ["sector", "region", "accounting_standard"],
              ["region", "accounting_standard"], ["accounting_standard"]]
    for columns in levels:
        for _, positions in frame.groupby(columns, dropna=False).groups.items():
            pending = [i for i in positions if pd.isna(result.loc[i])]
            observed = frame.loc[pending, value].dropna()
            if len(observed) < minimum_peers: continue
            lo, hi = observed.quantile([.05, .95]); clipped = observed.clip(lo, hi)
            result.loc[observed.index] = clipped.rank(pct=True, method="average", ascending=higher_is_better)
    return result

BASELINE_LENS_WEIGHTS = {"momentum": .30, "value": .25, "quality": .25, "catalyst": .20}

def derive_fundamental_metrics(values: dict[str, float | None], *, price: float | None = None) -> dict[str, float | None]:
    """Derive comparable metrics without converting unavailable inputs to zero."""
    def ratio(a: str, b: str) -> float | None:
        numerator, denominator = values.get(a), values.get(b)
        return None if numerator is None or denominator in (None, 0) else float(numerator / denominator)
    revenue=values.get("revenue"); prior_revenue=values.get("prior_revenue")
    shares=values.get("shares_outstanding"); debt=values.get("debt"); cash=values.get("cash")
    market_cap=None if price is None or shares is None else price*shares
    enterprise_value=None if market_cap is None or debt is None or cash is None else market_cap+debt-cash
    fcf=None if values.get("operating_cash_flow") is None or values.get("capital_expenditure") is None else values["operating_cash_flow"]-abs(values["capital_expenditure"])
    invested=None if debt is None or values.get("equity") is None or cash is None else debt+values["equity"]-cash
    return {
      "revenue_growth": None if revenue is None or prior_revenue in (None,0) else revenue/prior_revenue-1,
      "operating_margin": ratio("operating_income","revenue"), "free_cash_flow":fcf,
      "market_cap":market_cap,"enterprise_value":enterprise_value,"roe":ratio("net_income","average_equity"),
      "roic":None if values.get("nopat") is None or invested in (None,0) else values["nopat"]/invested,
      "debt_to_assets":ratio("debt","assets"),"liabilities_to_assets":ratio("liabilities","assets"),
      "interest_coverage":ratio("operating_income","interest_expense"),
      "earnings_yield":None if market_cap in (None,0) or values.get("net_income") is None else values["net_income"]/market_cap,
      "fcf_yield":None if market_cap in (None,0) or fcf is None else fcf/market_cap,
      "book_to_market":None if market_cap in (None,0) or values.get("equity") is None else values["equity"]/market_cap,
      "ev_to_operating_income":None if enterprise_value in (None,0) or values.get("operating_income") is None else enterprise_value/values["operating_income"],
      "cash_conversion":ratio("operating_cash_flow","net_income"),
      "accruals":None if values.get("net_income") is None or values.get("operating_cash_flow") is None or values.get("assets") in (None,0) else (values["net_income"]-values["operating_cash_flow"])/values["assets"],
      "dilution":None if shares is None or values.get("prior_shares_outstanding") in (None,0) else shares/values["prior_shares_outstanding"]-1,
      "earnings_stability":values.get("earnings_stability"),"cash_flow_stability":values.get("cash_flow_stability"),
    }

def momentum_features(local_prices: pd.Series, gbp_prices: pd.Series | None = None) -> dict[str, float | None]:
    """Multiple-horizon momentum and risk from already point-in-time aligned prices."""
    local=pd.Series(local_prices,dtype=float).dropna()
    def ret(series: pd.Series, days: int) -> float | None:
        return None if len(series)<=days or series.iloc[-days-1]<=0 else series.iloc[-1]/series.iloc[-days-1]-1
    changes=local.pct_change().dropna(); vol=None if len(changes)<21 else float(changes.iloc[-63:].std(ddof=0)*np.sqrt(252))
    result={f"local_momentum_{days}d":ret(local,days) for days in (21,63,126,252)}
    result["short_term_reversal"] = None if result["local_momentum_21d"] is None else -result["local_momentum_21d"]
    result["annualized_volatility"] = vol
    result["volatility_adjusted_momentum"] = None if vol in (None,0) or result["local_momentum_126d"] is None else result["local_momentum_126d"]/vol
    result["max_drawdown"] = None if local.empty else float((local/local.cummax()-1).min())
    result["abnormal_gap"] = None if changes.empty else float(changes.abs().iloc[-63:].max())
    gbp=pd.Series(dtype=float) if gbp_prices is None else pd.Series(gbp_prices,dtype=float).dropna()
    result.update({f"gbp_momentum_{days}d":ret(gbp,days) for days in (21,63,126,252)})
    return result

def score_candidates(features: pd.DataFrame, *, minimum_confidence: float = .55) -> pd.DataFrame:
    """Explainable baseline score. Risk is exclusively a penalty/gate."""
    required = set(BASELINE_LENS_WEIGHTS) | {"security_id", "risk_penalty", "coverage", "freshness", "hard_exclusion"}
    if missing := required - set(features.columns): raise ValueError(f"Missing feature columns: {sorted(missing)}")
    out = features.copy()
    for lens in BASELINE_LENS_WEIGHTS:
        out[f"{lens}_contribution"] = out[lens] * BASELINE_LENS_WEIGHTS[lens]
    out["confidence"] = (out.coverage.clip(0, 1) * .7 + out.freshness.clip(0, 1) * .3) * (1-out.risk_penalty.clip(0, 1)*.5)
    out["overall_score"] = sum(out[f"{x}_contribution"] for x in BASELINE_LENS_WEIGHTS) - out.risk_penalty.clip(0, 1)
    out["eligible"] = ~out.hard_exclusion.astype(bool) & out.confidence.ge(minimum_confidence)
    out["exclusion_reason"] = np.where(out.hard_exclusion, "hard risk gate", np.where(out.confidence.lt(minimum_confidence), "insufficient confidence", ""))
    out = out.sort_values(["eligible", "overall_score", "security_id"], ascending=[False, False, True])
    out["rank"] = pd.array([i+1 if ok and i < 3 else pd.NA for i, ok in enumerate(out.eligible)], dtype="Int64")
    return out

def learn_constrained_weights(training: pd.DataFrame, *, cutoff: datetime,
                              minimum_periods: int = 12) -> dict[str, float]:
    """Fit stable non-negative lens weights using observations strictly before cutoff.

    Callers perform the outer walk-forward split; this boundary check prevents an
    evaluation row from entering the nested training sample.
    """
    required=set(BASELINE_LENS_WEIGHTS)|{"as_of","forward_return"}
    if missing:=required-set(training.columns): raise ValueError(f"Missing training columns: {sorted(missing)}")
    frame=training.copy(); frame["as_of"]=pd.to_datetime(frame.as_of,utc=True)
    boundary=pd.Timestamp(cutoff)
    if boundary.tzinfo is None: boundary=boundary.tz_localize("UTC")
    frame=frame.loc[frame.as_of.lt(boundary)].dropna(subset=[*BASELINE_LENS_WEIGHTS,"forward_return"])
    if frame.as_of.dt.to_period("M").nunique()<minimum_periods: raise ValueError("Insufficient pre-cutoff training periods")
    model=Ridge(alpha=1.0,positive=True,fit_intercept=True).fit(frame[list(BASELINE_LENS_WEIGHTS)],frame.forward_return)
    coefficients=np.clip(model.coef_,0,.60)
    if coefficients.sum()<=0: raise ValueError("Learned coefficients are unstable")
    coefficients=coefficients/coefficients.sum()
    # Blend toward the documented baseline to control coefficient instability.
    baseline=np.array(list(BASELINE_LENS_WEIGHTS.values()))
    stable=.75*coefficients+.25*baseline
    return dict(zip(BASELINE_LENS_WEIGHTS,(stable/stable.sum()).tolist()))

class GlobalResearchRepository:
    def __init__(self, path: Path | str): self.path = Path(path)
    def initialize(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with duckdb.connect(str(self.path)) as db: db.execute(SCHEMA_SQL)
    def import_facts(self, facts: Iterable[RawFundamentalFact]) -> dict:
        facts = list(facts); self.initialize()
        with duckdb.connect(str(self.path)) as db:
            db.execute("BEGIN")
            try:
                for f in facts:
                    jurisdiction=f.security_id.split(":",1)[0] if ":" in f.security_id else "UNKNOWN"
                    db.execute("INSERT OR IGNORE INTO research_filing_reports VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                      [f.report_id,f.security_id,jurisdiction,f.accounting_standard,None,f.period_start,f.period_end,
                       utc_naive(f.published_at),utc_naive(f.available_at),utc_naive(f.retrieved_at),f.source_document,
                       f.source_provider,json.dumps(f.provenance,sort_keys=True),None,bool(f.revision_of_fact_id)])
                    db.execute("INSERT OR IGNORE INTO research_raw_fundamental_facts VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                      [f.fact_id,f.report_id,f.security_id,f.source_concept,f.value,f.currency,f.unit,f.period_start,f.period_end,
                       utc_naive(f.published_at),utc_naive(f.available_at),utc_naive(f.retrieved_at),f.accounting_standard,
                       f.source_document,f.source_provider,json.dumps(f.provenance,sort_keys=True),f.revision_of_fact_id,f.state])
                    metric=normalize_metric(f.source_concept)
                    if metric:
                        nid=hashlib.sha256((f.fact_id+FEATURE_VERSION).encode()).hexdigest()
                        db.execute("INSERT OR IGNORE INTO research_normalized_fundamentals VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                          [nid,f.fact_id,f.security_id,metric,f.value,f.currency,f.unit,f.period_start,f.period_end,
                           utc_naive(f.available_at),utc_naive(f.retrieved_at),FEATURE_VERSION,f.state,"taxonomy mapping"])
                db.execute("COMMIT")
            except BaseException: db.execute("ROLLBACK"); raise
        return {"command":"research_import","status":"completed","facts":len(facts)}
    def facts_as_of(self, at: datetime) -> list[dict]:
        if not self.path.exists(): return []
        with duckdb.connect(str(self.path), read_only=True) as db:
            tables={r[0] for r in db.execute("SHOW TABLES").fetchall()}
            if "research_normalized_fundamentals" not in tables: return []
            rows=db.execute("""SELECT security_id,metric,value,currency,unit,period_end,available_at,retrieved_at,state,fact_id
              FROM research_normalized_fundamentals WHERE available_at<=? AND retrieved_at<=?
              QUALIFY row_number() OVER(PARTITION BY security_id,metric ORDER BY available_at DESC,retrieved_at DESC,period_end DESC)=1""",
              [utc_naive(at),utc_naive(at)]).fetchall()
        keys=("security_id","metric","value","currency","unit","period_end","available_at","retrieved_at","state","fact_id")
        return [dict(zip(keys,r)) for r in rows]
    def create_vintage(self, *, universe_snapshot_id: str, evaluated_at: datetime, scored: pd.DataFrame,
                       horizon_days: int=21, model_version: str=BASELINE_VERSION) -> dict:
        payload=[]
        for _,r in scored.iterrows():
            lenses={x:float(r[x]) for x in BASELINE_LENS_WEIGHTS}; contributions={x:float(r[f"{x}_contribution"]) for x in BASELINE_LENS_WEIGHTS}
            payload.append({"security_id":r.security_id,"rank":None if pd.isna(r["rank"]) else int(r["rank"]),
              "score":float(r.overall_score),"confidence":float(r.confidence),"eligible":bool(r.eligible),"lenses":lenses,
              "contributions":contributions,"exclusions":([r.exclusion_reason] if r.exclusion_reason else []),"evidence":[]})
        digest=hashlib.sha256(json.dumps(payload,sort_keys=True,separators=(",", ":")).encode()).hexdigest()
        vid=hashlib.sha256(f"{model_version}|{universe_snapshot_id}|{evaluated_at.isoformat()}|{digest}".encode()).hexdigest()
        self.initialize()
        with duckdb.connect(str(self.path)) as db:
            existing=db.execute("SELECT content_hash FROM research_shadow_vintages WHERE vintage_id=?",[vid]).fetchone()
            if existing and existing[0]!=digest: raise ValueError("Research vintage is immutable")
            if not existing:
                db.execute("INSERT INTO research_shadow_vintages VALUES (?,?,?,?,?,?,?,?,?)",[vid,model_version,universe_snapshot_id,FEATURE_VERSION,utc_naive(evaluated_at),horizon_days,json.dumps({"weights":BASELINE_LENS_WEIGHTS},sort_keys=True),digest,utc_naive(datetime.now(timezone.utc))])
                for r in payload: db.execute("INSERT INTO research_shadow_candidates VALUES (?,?,?,?,?,?,?,?,?,?)",[vid,r["security_id"],r["rank"],r["score"],r["confidence"],r["eligible"],json.dumps(r["lenses"],sort_keys=True),json.dumps(r["contributions"],sort_keys=True),json.dumps(r["exclusions"]),json.dumps(r["evidence"])])
        return {"command":"shadow_vintage","status":"already_exists" if existing else "completed","vintage_id":vid,"qualified":sum(x["rank"] is not None for x in payload)}
    def latest_vintage(self) -> dict | None:
        if not self.path.exists(): return None
        with duckdb.connect(str(self.path),read_only=True) as db:
            if "research_shadow_vintages" not in {x[0] for x in db.execute("SHOW TABLES").fetchall()}: return None
            v=db.execute("SELECT vintage_id,model_version,universe_snapshot_id,feature_version,evaluated_at,horizon_days FROM research_shadow_vintages ORDER BY evaluated_at DESC LIMIT 1").fetchone()
            if not v:return None
            rows=db.execute("SELECT security_id,rank,overall_score,confidence,eligible,lens_scores_json,contributions_json,exclusions_json,evidence_json FROM research_shadow_candidates WHERE vintage_id=? ORDER BY rank NULLS LAST,security_id",[v[0]]).fetchall()
        return {"vintage_id":v[0],"model_version":v[1],"universe_snapshot_id":v[2],"feature_version":v[3],"evaluated_at":v[4],"horizon_days":v[5],"candidates":[{"security_id":r[0],"rank":r[1],"overall_score":r[2],"confidence":r[3],"eligible":r[4],"lens_scores":json.loads(r[5]),"contributions":json.loads(r[6]),"exclusions":json.loads(r[7]),"evidence":json.loads(r[8])} for r in rows]}
    def status(self) -> dict:
        if not self.path.exists(): return {"status":"unavailable","facts":0,"vintages":0,"regions":{"US":"unavailable","UK":"operator_file_required","CA":"operator_file_required","EU":"operator_file_required"}}
        with duckdb.connect(str(self.path),read_only=True) as db:
            tables={x[0] for x in db.execute("SHOW TABLES").fetchall()}
            count=lambda t: db.execute(f"SELECT count(*) FROM {t}").fetchone()[0] if t in tables else 0
            return {"status":"available" if count("research_raw_fundamental_facts") else "unavailable","facts":count("research_raw_fundamental_facts"),"vintages":count("research_shadow_vintages"),"regions":{"US":"sec_or_operator_file","UK":"operator_file_required","CA":"operator_file_required","EU":"operator_file_required"}}
