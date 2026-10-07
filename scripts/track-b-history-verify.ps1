# Complete Windows PowerShell 5.1 verification. Stop concurrent DB writers first.
# Run from any directory after checking out PR #94's current branch.
& {
  $ErrorActionPreference = "Stop"
  $Repo = "C:\Users\Juan Estrada\Projects\SignalLens"
  $Backend = Join-Path $Repo "backend"
  $Python = "..\.venv\Scripts\python.exe"
  $Pushed = $false
  try {
    Push-Location $Backend
    $Pushed = $true
    $Verify = @'
import hashlib, json, pathlib, re, subprocess, sys

paths = {'research': pathlib.Path('data/research/signallens-research.duckdb'),
         'production': pathlib.Path('data/signallens.duckdb')}
decisions = ('2026-10-04T21:30:00+00:00', '2026-10-05T00:30:00+00:00')
events = []
failed = False

def emit(stage, reason, **counts):
    events.append({'stage': stage, 'reason_code': reason, 'counts': counts})

def fingerprint(path):
    digest = hashlib.sha256()
    with path.open('rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            digest.update(block)
    return digest.hexdigest()

before = {}
try:
    for name, path in paths.items():
        try:
            before[name] = fingerprint(path)
            emit(name+'.external.before', 'FINGERPRINT_OK')
        except Exception:
            failed = True
            emit(name+'.external.before', 'FINGERPRINT_READ_FAILED')
    tests = ('test_track_b_history_market_stream.py','test_track_b_history_diagnostic.py',
             'test_track_b_history.py','test_track_b_panel.py','test_investment_research.py',
             'test_financial_strength.py','test_liquidity_materialization.py')
    run = subprocess.run([sys.executable,'-m','pytest', *('tests/'+t for t in tests), '-q'],
                         capture_output=True, text=True)
    if run.returncode:
        failed = True
        emit('offline_regression', 'OFFLINE_REGRESSION_FAILED')
    else:
        match = re.search(r'(\d+) passed', run.stdout)
        emit('offline_regression','OFFLINE_REGRESSION_PASSED',passed_count=int(match.group(1)) if match else 0)
    if len(before)==2 and run.returncode==0:
        for index, decision in enumerate(decisions):
            stage = 'boundary_'+str(index)
            try:
                command = [sys.executable,'-m','app.investment_research_cli',
                           'track-b-historical-evidence-inventory',
                           '--research-db',str(paths['research']),
                           '--production-db',str(paths['production']), '--decision-at',decision]
                result = subprocess.run(command,capture_output=True,text=True)
                if result.returncode:
                    failed = True
                    emit(stage,'INVENTORY_FAILED')
                    continue
                r = json.loads(result.stdout)
                assert r['command']=='track-b-historical-evidence-inventory'
                assert r['version']=='track-b-history-inventory-1.1.0'
                assert r['decision_at']==decision
                assert r['read_only'] and r['metadata_only']
                assert not r['realized_outcome_values_read'] and not r['model_executed']
                assert not r['panel_persisted'] and not r['acquisition_authorized']
                assert r['contract_selected'] is None and r['sample_thresholds'] is None
                assert r['validation_credit']==0 and not r['preregistration_ready']
                assert r['unresolved_requirement_count']==len(r['blockers'])==8
                assert all(b['state']=='unresolved' for b in r['blockers'])
                for key in ('scores','rankings','candidates','recommendations','allocations','selections',
                            'paper_selections','vintages','prospective_vintages','validation_observations'):
                    assert r[key]==[]
                compact = len(json.dumps(r,sort_keys=True,separators=(',',':')).encode('utf-8'))
                assert compact==r['compact_utf8_bytes']<=r['bounds']['maximum_compact_utf8_bytes']==131072
                assert r['bounds']['maximum_metadata_rows_per_table']==500000
                assert r['bounds']['maximum_chain_work_per_call']==50000
                b = r['bounds']['market_metadata']
                assert b['method']=='complete_ordered_stream' and b['batch_rows']==2048
                assert b['maximum_visible_dates_per_symbol']==50000
                assert b['maximum_metadata_cell_characters']==1024
                assert b['sql_memory_limit']=='128MB' and b['sql_threads']==1 and not b['disk_spill_allowed']
                for name in paths:
                    fp = r['database_fingerprints'][name]
                    assert fp['unchanged'] and fp['before']==fp['after']
                    assert fp['before']['sha256']==before[name]
                    for layer, expected in (('canonical_history','canonical'),('raw_sec_history','raw_sec')):
                        history = r['databases'][name][layer]
                        assert history['evidence_layer']==expected
                        for family in history['families'].values():
                            assert sum(family['primary_gap_counts'].values())==family['population_denominator']
                            assert family['certified_formula_count']==0
                            s=family['samples']
                            assert s['returned_count']==len(s['items'])<=s['sample_limit']==10
                            assert s['total_count']==family['securities_with_structural_chain']
                            assert s['truncated']==(s['total_count']>s['returned_count'])
                    source=next(s for s in r['sources'] if s['database']==name and s['table']=='global_price_observations')
                    market=r['databases'][name]['market_metadata']['price_and_risk']
                    if source['state']!='incompatible_evidence':
                        assert sum(market['metadata_observation_states'].values())==source['row_count']
                        emit(stage+'.'+name+'.global_price_observations','MARKET_COUNTS_VERIFIED',
                             row_count=source['row_count'],
                             visible_distinct_symbol_date_count=market['boundary_visible_distinct_symbol_date_count'],
                             symbols_with_253_dates=market['symbols_with_253_date_metadata_rows'])
                    else:
                        emit(stage+'.'+name+'.global_price_observations','SCHEMA_INCOMPATIBLE')
                    canonical=next(s for s in r['sources'] if s['database']==name and s['table']=='canonical_factor_evidence')
                    emit(stage+'.'+name+'.canonical_factor_evidence','ADAPTER_SCHEMA_COUNT',
                         missing_column_count=len(canonical.get('missing_adapter_columns',())))
                repeat=subprocess.run(command,capture_output=True,text=True)
                assert repeat.returncode==0 and repeat.stdout==result.stdout
                emit(stage,'INVENTORY_VERIFIED',compact_utf8_bytes=compact)
            except Exception:
                failed=True
                emit(stage,'VERIFICATION_FAILED')
except Exception:
    failed=True
    emit('verification','VERIFICATION_FAILED')
finally:
    for name,path in paths.items():
        try:
            after=fingerprint(path)
            if name not in before:
                failed=True
                emit(name+'.external.after','FINGERPRINT_BASELINE_UNAVAILABLE')
            elif after!=before[name]:
                failed=True
                emit(name+'.external.after','FINGERPRINT_CHANGED')
            else:
                emit(name+'.external.after','FINGERPRINT_UNCHANGED')
        except Exception:
            failed=True
            emit(name+'.external.after','FINGERPRINT_READ_FAILED')
print(json.dumps({'events':events},sort_keys=True,separators=(',',':')))
sys.exit(int(failed))
'@
    & $Python -c $Verify 2>$null
    if ($LASTEXITCODE -ne 0) { Write-Host "verification VERIFICATION_NONZERO" }
  } catch { Write-Host "verification VERIFICATION_FAILED" }
  finally { if ($Pushed) { Pop-Location } }
}
