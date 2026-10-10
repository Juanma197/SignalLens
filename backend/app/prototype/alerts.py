"""Daily holding alerts: message only when a decision changes.

Each run (weekdays before the US open, on the previous close):
1. optionally updates prices and dividends from EODHD's whole-market files
   (app.daily_prices, two requests per trading day);
2. optionally checks each held company's SEC submissions (one request each) and,
   when a new 10-K or 10-Q is listed, re-reads its SEC figures;
3. recomputes the monthly view at the current time (same rules as the page);
4. compares every holding's (decision, thesis status) with the last one sent and
   messages only the changes, plus a short weekly summary on Fridays;
5. once a month (see `monthly_cycle`): records the month's picks and decisions
   for the scorecard, sends a monthly review, and reminds about an unrecorded
   contribution from the 8th;
6. reassesses the cash pool (see `reassess`).
Holding messages concern something already owned. The one message about new
money is the cash reassessment: it is sent only when the cash you actually have
(no unexecuted sales counted) has a worthwhile use that differs from the last
suggestion sent, so an unchanged suggestion is never repeated.
The first run sends one "alerts are on" message with every current decision.

Decision support only: nothing is executed, and the rules are unvalidated.

    python -m app.prototype.alerts --dry-run          print what would be sent
    python -m app.prototype.alerts                    send via Telegram
    python -m app.prototype.alerts --telegram-chat-id print your chat id (message your bot first)
Environment: SIGNALLENS_TELEGRAM_BOT_TOKEN, SIGNALLENS_TELEGRAM_CHAT_ID, optional
SIGNALLENS_PUBLIC_URL, SIGNALLENS_EODHD_API_TOKEN, SIGNALLENS_SEC_USER_AGENT.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import os
import sys
from urllib.parse import quote

import duckdb
import httpx

MAX_MESSAGE = 3900  # Telegram allows 4096 characters
PERIODIC_FORMS = {'10-K', '10-K/A', '10-Q', '10-Q/A'}
SUMMARY_WEEKDAY = 4  # Friday
DISCLAIMER = 'Decision support only; nothing has been traded. Unvalidated rules.'


def _pct(value):
    return '—' if value is None else f'{value * 100:+.0f}%'


def _money(value):
    return '—' if value is None else f'${value:,.0f}'


def _size_line(h, monthly):
    """How much the decision means in shares and dollars."""
    if h['decision'] == 'SELL' and h.get('market_value') is not None:
        return f"Sell all {h['shares']:g} shares (≈ {_money(h['market_value'])})."
    if h['decision'] == 'REDUCE':
        sale = next((s for s in (monthly.get('allocation') or {}).get('sales', []) if s['qualified_symbol'] == h['qualified_symbol']), None)
        if sale and h['shares']:
            return f"Reduce by {sale['shares']:g} shares (≈ {_money(sale['amount'])}, {sale['shares'] / h['shares']:.0%} of the position)."
    if h['decision'] == 'BUY MORE' and h.get('weight') and h.get('market_value'):
        total = h['market_value'] / h['weight']
        room = monthly['rules']['maximum_position_weight_for_buying'] * total - h['market_value']
        if room > 0: return f"Room to add ≈ {_money(room)} before the {monthly['rules']['maximum_position_weight_for_buying']:.0%} position limit."
    return None


def _thesis(h):
    return (h.get('checks') or {}).get('overall', 'not_covered')


def change_message(h, previous, monthly, link=None):
    before = f'{previous[0]} → ' if previous else ''
    lines = [f"{h['qualified_symbol']}: {before}{h['decision']}"]
    size = _size_line(h, monthly)
    if size: lines.append(size)
    lines += [f'• {r}' for r in h['reasons'][:4]]
    lines.append(f"Upside {_pct(h['evidence'].get('upside'))} · weight {_pct(h.get('weight')).lstrip('+')} · thesis {_thesis(h).replace('_', ' ')}")
    if link: lines.append(link)
    return '\n'.join(lines)


def summary_message(monthly, title, link=None):
    lines = [title]
    for h in monthly['holdings']:
        lines.append(f"{h['qualified_symbol']}: {h['decision']} (upside {_pct(h['evidence'].get('upside'))}, thesis {_thesis(h).replace('_', ' ')})")
    if not monthly['holdings']: lines.append('No holdings recorded yet: add them on the Portfolio page.')
    if link: lines.append(link)
    lines.append(DISCLAIMER)
    return '\n'.join(lines)


TRIGGERS = {'sale': 'after your sale of {symbol}', 'deposit': 'after your deposit', 'daily': 'daily check'}


def cash_message(monthly, trigger, *, symbol=None, link=None):
    """(fingerprint, text) when the cash pool has a worthwhile use, else None.

    `monthly` must be computed without reinvesting suggested sales, so every
    suggested buy is paid for with money already in the account. The fingerprint
    is the money available (in steps of 10) and the suggested names, so a new
    deposit or sale, or a different best use, gives a new message."""
    plan = monthly.get('allocation') or {}
    account, buys = plan.get('account') or {}, plan.get('buys') or []
    def money(v): return '—' if v is None else f"£{v:,.0f}"
    check = account.get('reconciliation') or {}
    if plan.get('held_back_buys') and check.get('blocks_buys'):
        # Sent once per difference: the buys stay held back until the balances agree.
        fingerprint = json.dumps({'mismatch': round(check['difference'], 2), 'as_of': check['as_of']})
        lines = ['Buys held back: ' + check['message']]
        if link: lines.append(link)
        return fingerprint, '\n'.join(lines)
    if not buys or not account: return None
    available = account['available']
    fingerprint = json.dumps({'available': int(available // 10) * 10, 'buys': sorted([b['action'], b['qualified_symbol']] for b in buys)})
    spent = sum(b.get('amount_gbp') or 0 for b in buys)
    lines = [f"Cash to put to work ({TRIGGERS[trigger].format(symbol=symbol or 'a holding')}): {money(available)} available."]
    for b in buys:
        lines.append(f"• {b['action']} {b['qualified_symbol']}: {money(b.get('amount_gbp'))} "
                     f"({b['shares']:g} shares at ~${b['price']:,.2f}). {b['why']}")
    lines.append(f"Leaves {money(available - spent)} as cash.")
    for p in plan.get('unfunded_picks') or []:
        lines.append(f"• Watch {p['qualified_symbol']} (pick #{p.get('rank')}): insufficient available cash after the buys above.")
    if account.get('awaiting_proceeds'):
        lines.append(f"About {money(account['awaiting_proceeds'])} more after your suggested sales are recorded; the plan is recalculated then.")
    if plan.get('skipped_no_slot'):
        lines.append(f"No free place for {', '.join(s['qualified_symbol'] for s in plan['skipped_no_slot'])} (holdings at the maximum).")
    stamp = monthly.get('plan') or {}
    if stamp:
        lines.append(f"Plan {stamp['version']}: assumes £{stamp['assumes']['cash_pool']:,.2f} cash and prices to "
                     f"{stamp['assumes']['prices_through'] or '—'}. Out of date once you record a trade, deposit, dividend or fee.")
    if link: lines.append(link)
    lines.append(DISCLAIMER)
    return fingerprint, '\n'.join(lines)


def reassess(trigger, *, symbol=None, notifier=None, now=None, dry_run=False, monthly=None):
    """Re-run the allocation on the cash actually held and message it if it is new.

    Called after a recorded sale or deposit and by the daily run. Returns what happened."""
    from ..config import get_settings
    from . import api
    from .store import PrototypeStore
    settings = get_settings()
    now = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    store = PrototypeStore(settings.prototype_database_path, protected_paths=(settings.research_database_path, settings.database_path))
    monthly = monthly or api.monthly(now, 15, False, False)
    base = os.environ.get('SIGNALLENS_PUBLIC_URL', '').rstrip('/')
    link = f"{base}/?decision_at={quote(monthly['decision_at'])}" if base else None
    planned = cash_message(monthly, trigger, symbol=symbol, link=link)
    if planned is None: return {'status': 'nothing_worthwhile'}
    fingerprint, text = planned
    if fingerprint == store.last_reallocation_fingerprint(): return {'status': 'unchanged'}
    if dry_run: return {'status': 'would_send', 'message': text}
    try: notifier.send(text); delivered = True
    except Exception: delivered = False  # not counted as sent, so the next reassessment tries again
    store.record_reallocation(trigger, fingerprint, text, delivered=delivered, now=now)
    return {'status': 'sent' if delivered else 'send_failed', 'message': text}


CONTRIBUTION_REMINDER_DAY = 8


def _gbp(value):
    return '—' if value is None else f'£{value:,.0f}'


def deposited_in(movements, period):
    """Pounds deposited (not voided) in calendar month `period` (YYYY-MM)."""
    return sum(float(m['amount']) for m in movements
               if m['kind'] == 'deposit' and not m.get('voided_at') and str(m['moved_on'])[:7] == period)


def monthly_review_message(monthly, *, now, deposited, contribution, recorded, link=None):
    lines = [f'SignalLens monthly review: {now:%B %Y}']
    if monthly['picks']:
        lines.append('Top picks:')
        lines += [f"#{p.get('rank')} {p['qualified_symbol']}: upside {_pct(p.get('upside'))} · conviction {p.get('conviction') or '—'} · "
                  f"risk {p.get('risk') or '—'}{' · already held' if p.get('held') else ''}" for p in monthly['picks']]
    else:
        lines.append('No company qualifies this month; keeping cash or your holdings is a valid outcome.')
    if monthly['holdings']:
        counts = {}
        for h in monthly['holdings']: counts[h['decision']] = counts.get(h['decision'], 0) + 1
        lines.append('Your holdings: ' + ' · '.join(f'{d} {n}' for d, n in counts.items()))
    else:
        lines.append('No holdings recorded yet.')
    account = (monthly.get('allocation') or {}).get('account') or {}
    status = (f'{_gbp(deposited)} deposited this month' if deposited else
              f'planned {_gbp(contribution)} not recorded yet' if contribution else 'no contribution planned')
    lines.append(f"Cash pool {_gbp(account.get('cash_pool'))}; {status}.")
    if recorded: lines.append("This month's picks and decisions are recorded for the scorecard.")
    if link: lines.append(link)
    lines.append(DISCLAIMER)
    return '\n'.join(lines)


def contribution_reminder(*, now, contribution, link=None):
    lines = [f"Reminder: your {_gbp(contribution)} SignalLens contribution for {now:%B} isn't recorded yet. "
             'Once it reaches your account, record it on the Portfolio page and the cash will be reassessed. '
             'If your plans changed, update the contribution there.']
    if link: lines.append(link)
    return '\n'.join(lines)


def monthly_cycle(store, monthly, *, notifier, now, dry_run=False, link=None, portfolio_link=None):
    """The once-a-month steps of the daily run; each happens at most once per calendar month."""
    from . import api
    from .scorecard import record_from_monthly
    from .store import StoreError
    period, result, messages = now.strftime('%Y-%m'), {}, []
    recorded = any(r['month'] == period for r in store.decision_records())
    if not recorded and not dry_run:
        record = record_from_monthly(monthly, api.report_at(now, 15))
        if record['benchmark_symbols']:
            try: store.create_decision_record(record, now=now); recorded = True; result['record'] = 'recorded'
            except StoreError as exc: result['record'] = exc.code
        else: result['record'] = 'no_assessed_companies'
    contribution = store.settings()['monthly_contribution']
    deposited = deposited_in(store.cash_movements(), period)
    planned = []
    if not store.cycle_sent('monthly_review', period):
        planned.append(('monthly_review', monthly_review_message(monthly, now=now, deposited=deposited, contribution=contribution,
                                                                 recorded=recorded, link=link)))
    if now.day >= CONTRIBUTION_REMINDER_DAY and contribution > 0 and not deposited and not store.cycle_sent('contribution_reminder', period):
        planned.append(('contribution_reminder', contribution_reminder(now=now, contribution=contribution, link=portfolio_link)))
    for kind, text in planned:
        if dry_run: messages.append(text); continue
        try: notifier.send(text); delivered = True
        except Exception: delivered = False  # retried on the next run
        store.record_cycle_event(kind, period, text, delivered=delivered, now=now)
        if delivered: messages.append(text)
    return result | {'messages': messages}


def plan_messages(monthly, previous, *, now, last_summary=None, link=None):
    """[(kind, symbol, decision, thesis, text)] to send, given the last states sent."""
    out = []
    if not previous:
        out.append(('baseline', None, None, None, summary_message(monthly, 'SignalLens alerts are on. Current decisions:', link)))
        out += [('baseline', h['qualified_symbol'], h['decision'], _thesis(h), '') for h in monthly['holdings']]
        return out
    for h in monthly['holdings']:
        state = (h['decision'], _thesis(h))
        if previous.get(h['qualified_symbol']) != state:
            out.append(('change', h['qualified_symbol'], *state, change_message(h, previous.get(h['qualified_symbol']), monthly, link)))
    due = now.weekday() == SUMMARY_WEEKDAY and (last_summary is None or (now - last_summary).days >= 6)
    if due: out.append(('summary', None, None, None, summary_message(monthly, f'SignalLens weekly summary ({now:%d %b}):', link)))
    return out


class TelegramNotifier:
    def __init__(self, token, chat_id, transport=None):
        if not token or not chat_id: raise ValueError('SIGNALLENS_TELEGRAM_BOT_TOKEN and SIGNALLENS_TELEGRAM_CHAT_ID are required')
        self.url, self.chat_id = f'https://api.telegram.org/bot{token}/sendMessage', str(chat_id)
        self.client = httpx.Client(timeout=20, transport=transport)

    def send(self, text):
        response = self.client.post(self.url, json={'chat_id': self.chat_id, 'text': text[:MAX_MESSAGE], 'disable_web_page_preview': True})
        if response.status_code != 200 or not response.json().get('ok'):
            raise RuntimeError(f'telegram_send_failed_{response.status_code}')


def held_filing_refresh(*, research, production, store, user_agent, now):
    """Re-read SEC figures for held companies that have a 10-K/10-Q not yet stored."""
    from ..sec_ingestion import AUTHORIZATION_PHRASE, BudgetClient, IngestionLimits, ingest
    from ..sec_capability import SEC_SUBMISSIONS
    from .portfolio import positions_from
    held, _ = positions_from(store.trades())
    symbols = sorted({p['qualified_symbol'] for p in held})
    if not symbols: return {'checked': 0, 'refreshed': []}
    with duckdb.connect(str(research), read_only=True) as db:
        marks = ','.join('?' for _ in symbols)
        issuers = db.execute(f'SELECT security_id, qualified_symbol, cik FROM sec_issuers WHERE qualified_symbol IN ({marks})', symbols).fetchall()
        stored = {r[0] for r in db.execute('SELECT accession_number FROM sec_filings').fetchall()}
    client = BudgetClient(user_agent, IngestionLimits(max_requests=max(1, len(issuers)), runtime_seconds=600))
    stale = set()
    for sid, symbol, cik in issuers:
        try:
            recent = client.get(SEC_SUBMISSIONS.format(cik=cik)).get('filings', {}).get('recent', {})
        except (RuntimeError, ValueError):
            continue
        accessions = [a for f, a in zip(recent.get('form', []), recent.get('accessionNumber', [])) if f in PERIODIC_FORMS]
        if accessions and accessions[0] not in stored: stale.add(sid)
    result = {'checked': len(issuers), 'refreshed': sorted(stale)}
    if stale:
        result['ingest'] = ingest(research=research, production=production, authorization=AUTHORIZATION_PHRASE, dry_run=False,
                                  limits=IngestionLimits(max_requests=2 * len(stale) + 1, runtime_seconds=600, max_response_bytes=10_000_000),
                                  security_ids=stale, refresh=True, now=now)
    return result


def run(*, notifier=None, dry_run=False, update=True, now=None):
    from ..config import get_settings
    from . import api
    from .store import PrototypeStore
    settings = get_settings()
    now = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    store = PrototypeStore(settings.prototype_database_path, protected_paths=(settings.research_database_path, settings.database_path))
    steps = {}
    if update and not dry_run:
        token, agent = os.environ.get('SIGNALLENS_EODHD_API_TOKEN', ''), os.environ.get('SIGNALLENS_SEC_USER_AGENT', '')
        if token:
            from ..daily_prices import update as update_prices
            from ..eodhd_ingestion import EODHDClient, EODHDLimits
            steps['prices'] = update_prices(research=settings.research_database_path, production=settings.database_path,
                                            client=EODHDClient(token, EODHDLimits(daily_requests=80, maximum_runtime_seconds=900)), now=now)
        if agent:
            steps['filings'] = held_filing_refresh(research=settings.research_database_path, production=settings.database_path,
                                                   store=store, user_agent=agent, now=now)
    monthly = api.monthly(now, 15, False, True)
    base = os.environ.get('SIGNALLENS_PUBLIC_URL', '').rstrip('/')
    link = f"{base}/?decision_at={quote(monthly['decision_at'])}" if base else None
    last_summary = store.last_alert_at('summary')
    last_summary = datetime.fromisoformat(last_summary) if isinstance(last_summary, str) else last_summary
    planned = plan_messages(monthly, store.alert_states(), now=now, last_summary=last_summary, link=link)
    sent, baseline_ok = [], True
    for kind, symbol, decision, thesis, text in planned:
        if dry_run:
            if text: sent.append(text)
            continue
        # Baseline state rows carry no text: they count as sent only if the baseline message was.
        delivered = baseline_ok
        if text:
            try: notifier.send(text); delivered = True
            except Exception: delivered = False  # recorded as undelivered, so the change is retried next run
            if kind == 'baseline': baseline_ok = delivered
        store.record_alert(kind, text or f'{symbol}: {decision}', delivered=delivered, qualified_symbol=symbol,
                           decision=decision, thesis=thesis, now=now)
        if text and delivered: sent.append(text)
    cycle = monthly_cycle(store, monthly, notifier=notifier, now=now, dry_run=dry_run, link=link,
                          portfolio_link=f'{base}/portfolio' if base else None)
    sent += cycle.pop('messages')
    if cycle: steps['monthly_cycle'] = cycle
    cash = reassess('daily', notifier=notifier, now=now, dry_run=dry_run)
    if cash.get('status') in ('sent', 'would_send'): sent.append(cash['message'])
    return {'command': 'alerts', 'dry_run': dry_run, 'decision_at': monthly['decision_at'], 'holdings': len(monthly['holdings']),
            'messages': sent, 'steps': steps}


def telegram_chat_id(token, transport=None):
    """Chat ids that have messaged the bot (send it any message first)."""
    if not token: raise ValueError('SIGNALLENS_TELEGRAM_BOT_TOKEN is required')
    with httpx.Client(timeout=20, transport=transport) as client:
        response = client.get(f'https://api.telegram.org/bot{token}/getUpdates')
    if response.status_code != 200: raise RuntimeError(f'telegram_get_updates_failed_{response.status_code}')
    updates = response.json().get('result', [])
    return sorted({(u.get('message') or {}).get('chat', {}).get('id') for u in updates if (u.get('message') or {}).get('chat')})


def main(argv=None):
    parser = argparse.ArgumentParser(description='Message holding decision changes to Telegram.')
    parser.add_argument('--dry-run', action='store_true', help='print the messages; send and record nothing')
    parser.add_argument('--skip-update', action='store_true', help='do not download prices or filings first')
    parser.add_argument('--telegram-chat-id', action='store_true', help='print the chat id of whoever messaged the bot')
    args = parser.parse_args(argv)
    token = os.environ.get('SIGNALLENS_TELEGRAM_BOT_TOKEN', '')
    try:
        if args.telegram_chat_id:
            print(json.dumps({'chat_ids': telegram_chat_id(token)})); return 0
        notifier = None if args.dry_run else TelegramNotifier(token, os.environ.get('SIGNALLENS_TELEGRAM_CHAT_ID', ''))
        result = run(notifier=notifier, dry_run=args.dry_run, update=not args.skip_update)
    except Exception as exc:  # tokens live in URLs; never print exception text
        print(json.dumps({'status': 'failed', 'error': type(exc).__name__}), file=sys.stderr)
        return 1
    print(json.dumps(result, indent=2, default=str, ensure_ascii=False))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
