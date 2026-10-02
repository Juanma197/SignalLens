from datetime import datetime, timezone
import pandas as pd
import pytest
from app.prospective_us_shadow import CONFIGURATION_HASH, score_inputs


def test_frozen_hash_and_exact_top3_reconciliation_and_symbol_tie_break():
    assert CONFIGURATION_HASH == "7b11264778fd120c03c820275d9c048d002bdb8564510fcf989cb590ce1b7ebd"
    prices=pd.DataFrame([
      {"security_id":x,"qualified_symbol":s,"price_percentile":p,"decision_price":10,"model_ready":True,"price_date":"2026-10-01"}
      for x,s,p in [("b","BBB.US",1.0),("a","AAA.US",1.0),("c","CCC.US",.5),("d","DDD.US",.1)]])
    dilution=pd.DataFrame([{"security_id":x,"diluted_share_growth":g,"period_end":"2025-12-31","filed_at":"2026-02-01T00:00:00Z","retrieved_at":"2026-02-02T00:00:00Z","reliable":True,"compatible":True,"provenance":"sec:x"} for x,g in [("a",0),("b",0),("c",.1)]])
    eligible,selected=score_inputs(prices,dilution,decision_at=datetime(2026,10,1,tzinfo=timezone.utc))
    assert selected.qualified_symbol.tolist()==["AAA.US","BBB.US","CCC.US"]
    assert len(selected)==3 and "d" not in set(eligible.security_id)
    for row in selected.itertuples():
      assert row.prospective_score == pytest.approx(.9*row.price_percentile+.1*row.dilution_percentile)


def test_post_decision_dilution_is_withheld_not_imputed():
    prices=pd.DataFrame([{"security_id":"a","qualified_symbol":"AAA.US","price_percentile":1.,"decision_price":10,"model_ready":True,"price_date":"2026-10-01"}])
    dilution=pd.DataFrame([{"security_id":"a","diluted_share_growth":0.,"period_end":"2025-12-31","filed_at":"2026-02-01T00:00:00Z","retrieved_at":"2026-10-02T00:00:00Z","reliable":True,"compatible":True,"provenance":"sec:x"}])
    eligible,selected=score_inputs(prices,dilution,decision_at=datetime(2026,10,1,tzinfo=timezone.utc))
    assert eligible.empty and selected.empty
