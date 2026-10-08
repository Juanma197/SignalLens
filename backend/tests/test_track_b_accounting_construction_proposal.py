"""UNAPPROVED synthetic design oracle. Run from repo root: python -B backend/tests/test_track_b_accounting_construction_proposal.py.
No application, database, provider, network or operator evidence access.
Semantic attestations are fixture inputs, never inferred from tag names.
"""
import unittest
from dataclasses import dataclass, replace
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal as D

class Refusal(ValueError): pass

@dataclass(frozen=True)
class Fact:
    amount: D
    start: date | None
    end: date
    concept: str
    public: datetime
    retrieved: datetime
    available: datetime
    scope: str = 'issuer-consolidated'
    basis: str = 'original'
    currency: str = 'USD'
    scale: D = D(1)
    provenance: bool = True
    input_layer: str = 'raw_sec'
    controlled: bool = False
    materialized: datetime | None = None

DECISION = datetime(2026, 10, 8, tzinfo=timezone.utc)
OLD = datetime(2026, 10, 1, tzinfo=timezone.utc)

def fact(amount, start=None, end=date(2026, 6, 30), concept='OCF', **kw):
    return Fact(D(str(amount)), start, end, concept, OLD, OLD, OLD, **kw)

def validate(f, decision=DECISION):
    if not f.provenance: raise Refusal('MISSING_STORED_EVIDENCE')
    if any(t.tzinfo is None or t.utcoffset() is None for t in (f.public, f.retrieved, f.available)):
        raise Refusal('visibility_unknown')
    expected=max(f.public,f.retrieved)
    if f.input_layer == 'canonical' and f.controlled:
        if f.materialized is None or f.materialized.tzinfo is None: raise Refusal('materialization_unknown')
        expected=max(expected,f.materialized)
    if f.available != expected: raise Refusal('availability_mismatch')
    if f.available > decision: raise Refusal('NOT_VISIBLE_AT_DECISION')
    if f.end > decision.date(): raise Refusal('period_after_decision')
    if f.currency != 'USD' or not f.scale.is_finite() or f.scale <= 0:
        raise Refusal('unit_or_scale')
    if not f.amount.is_finite(): raise Refusal('invalid_amount')
    return f.amount * f.scale

def compatible(a, b):
    if (a.concept, a.scope, a.basis, a.currency) != (b.concept, b.scope, b.basis, b.currency):
        raise Refusal('incompatible_basis')

def quarter(current, previous=None, *, boundaries, ytd_attested, decision=DECISION):
    if not ytd_attested or current.start != boundaries[0]: raise Refusal('fiscal_evidence')
    if current.end not in boundaries[1:]: raise Refusal('quarter_boundary')
    k = boundaries.index(current.end)
    value = validate(current,decision)
    if current.concept == 'CAPEX' and value < 0: raise Refusal('capex_sign_unexplained')
    if k == 1:
        if previous is not None: raise Refusal('unexpected_predecessor')
        return replace(current, amount=value, scale=D(1))
    if previous is None: raise Refusal('MISSING_STORED_EVIDENCE')
    compatible(current, previous)
    if previous.start != current.start or previous.end != boundaries[k-1]:
        raise Refusal('predecessor_period')
    value -= validate(previous,decision)
    if current.concept == 'CAPEX' and value < 0: raise Refusal('capex_sign_unexplained')
    return replace(current, amount=value, start=previous.end+timedelta(days=1),
                   scale=D(1), available=max(current.available, previous.available),
                   public=max(current.public, previous.public), retrieved=max(current.retrieved, previous.retrieved))

def ttm(quarters):
    if len(quarters) != 4: raise Refusal('four_quarters_required')
    if any(q.start is None for q in quarters): raise Refusal('duration_required')
    for a, b in zip(quarters, quarters[1:]):
        compatible(a, b)
        if b.start != a.end+timedelta(days=1): raise Refusal('gap_or_overlap')
    return sum((validate(q) for q in quarters), D(0))

def paired_ttm(ocf, capex):
    if [(q.start,q.end) for q in ocf] != [(q.start,q.end) for q in capex]:
        raise Refusal('asymmetric_periods')
    return ttm(ocf), ttm(capex)

def debt(components, *, disjoint, exhaustive):
    if not disjoint or not exhaustive: raise Refusal('debt_coverage_unproven')
    if len({f.end for f in components}) != 1 or any(f.start is not None for f in components):
        raise Refusal('instant_alignment')
    if len({(f.scope,f.basis) for f in components}) != 1: raise Refusal('debt_basis')
    values = [validate(f) for f in components]
    if any(v < 0 for v in values): raise Refusal('negative_debt')
    return sum(values, D(0))

def draft_current(f):
    validate(f)
    if f.concept != 'ShortTermBorrowings': raise Refusal('UNSUPPORTED_DRAFT_CONSTRUCTION')
    return f.amount*f.scale

def cash_direct(f, canonical_field=None):
    value=validate(f)
    if f.start is not None or f.concept != 'CashAndCashEquivalentsAtCarryingValue' or value < 0:
        raise Refusal('not_direct_cash')
    if canonical_field is not None and canonical_field != 'cash_and_cash_equivalents':
        raise Refusal('UNSUPPORTED_DRAFT_CONSTRUCTION')
    return value

def cash_residual(combined, restricted, *, exhaustive):
    if not exhaustive: raise Refusal('restricted_coverage_unproven')
    all_facts=[combined]+restricted
    if any(f.start is not None for f in all_facts) or len({(f.end,f.scope,f.basis) for f in all_facts}) != 1:
        raise Refusal('cash_alignment')
    values=[validate(f) for f in all_facts]
    if any(v < 0 for v in values): raise Refusal('negative_component')
    result=values[0]-sum(values[1:],D(0))
    if result < 0: raise Refusal('negative_residual')
    return result

class ContractTests(unittest.TestCase):
    def setUp(self):
        self.calendar=(date(2026,1,1),date(2026,3,31),date(2026,6,30),date(2026,9,30),date(2026,12,31))
        self.q1=fact(30,self.calendar[0],self.calendar[1])
        self.q2=fact(70,self.calendar[0],self.calendar[2])
    def construct(self, a, b=None, attested=True):
        return quarter(a,b,boundaries=self.calendar,ytd_attested=attested)
    def test_standalone_and_scale(self):
        self.assertEqual(self.construct(self.q2,self.q1).amount,D(40))
        scaled=replace(self.q1,amount=D('.03'),scale=D(1000))
        self.assertEqual(self.construct(self.q2,scaled).amount,D(40))
    def test_missing_not_zero_and_calendar(self):
        for a,b,attested in [(self.q2,None,True),(self.q2,self.q1,False),
                              (replace(self.q2,start=date(2026,1,2)),self.q1,True)]:
            with self.subTest(a=a,b=b), self.assertRaises(Refusal): self.construct(a,b,attested)
    def test_recast_and_scope(self):
        for bad in [replace(self.q2,basis='recast'),replace(self.q2,scope='segment')]:
            with self.assertRaises(Refusal): self.construct(bad,self.q1)
        self.assertEqual(self.construct(replace(self.q2,amount=D(75),basis='recast'),
                                       replace(self.q1,amount=D(35),basis='recast')).amount,D(40))
    def test_visibility(self):
        later=datetime(2026,8,12,tzinfo=timezone.utc)
        for bad in [replace(self.q1,public=datetime(2026,8,10,tzinfo=timezone.utc),retrieved=later,available=later),
                    replace(self.q1,retrieved=later),replace(self.q1,public=OLD.replace(tzinfo=None))]:
            with self.assertRaises(Refusal): validate(bad, datetime(2026,8,11,tzinfo=timezone.utc))
    def test_unit_and_provenance(self):
        for bad in [replace(self.q1,currency='EUR'),replace(self.q1,scale=D(0)),
                    replace(self.q1,amount=D('NaN')),replace(self.q1,provenance=False)]:
            with self.assertRaises(Refusal): validate(bad)
    def test_ttm_and_pairing(self):
        starts=[date(2025,10,1),date(2026,1,1),date(2026,4,1),date(2026,7,1)]
        ends=[date(2025,12,31),date(2026,3,31),date(2026,6,30),date(2026,9,30)]
        ocf=[fact(v,s,e) for v,s,e in zip([20,30,40,20],starts,ends)]
        cap=[fact(v,s,e,'CAPEX') for v,s,e in zip([8,10,15,15],starts,ends)]
        self.assertEqual(paired_ttm(ocf,cap),(D(110),D(48)))
        for bad in [ocf[:3],ocf[:3]+[ocf[2]],ocf[:3]+[replace(ocf[3],start=date(2026,7,2))]]:
            with self.assertRaises(Refusal): ttm(bad)
        with self.assertRaises(Refusal): paired_ttm(ocf,cap[:3])
    def test_signed_ocf_and_capex(self):
        self.assertEqual(self.construct(replace(self.q1,amount=D(-5))).amount,D(-5))
        with self.assertRaises(Refusal):
            self.construct(replace(self.q2,concept='CAPEX',amount=D(5)),replace(self.q1,concept='CAPEX',amount=D(10)))
    def test_debt_overlap_and_semantics(self):
        st=fact(12,concept='ShortTermBorrowings'); cur=fact(8,concept='LongTermDebtCurrent')
        non=fact(80,concept='LongTermDebtNoncurrent')
        self.assertEqual(debt([st,cur,non],disjoint=True,exhaustive=True),D(100))
        self.assertEqual(debt([st,fact(88,concept='inclusive_LTD')],disjoint=True,exhaustive=True),D(100))
        with self.assertRaises(Refusal): debt([st,cur,fact(88)],disjoint=False,exhaustive=True)
        with self.assertRaisesRegex(Refusal,'UNSUPPORTED'): draft_current(cur)
        with self.assertRaises(Refusal): debt([st,replace(non,end=date(2026,3,31))],disjoint=True,exhaustive=True)
        with self.assertRaises(Refusal): debt([st,non],disjoint=True,exhaustive=False)
    def test_cash_direct_separate_field(self):
        direct=fact(50,concept='CashAndCashEquivalentsAtCarryingValue')
        self.assertEqual(cash_direct(direct),D(50))
        with self.assertRaisesRegex(Refusal,'UNSUPPORTED'): cash_direct(direct,'unrestricted_cash')
        with self.assertRaises(Refusal): cash_direct(fact(65,concept='combined_cash'))
    def test_controlled_canonical_invisible_before_materialization(self):
        late=DECISION.replace(day=9)
        controlled=replace(self.q1,input_layer='canonical',controlled=True,materialized=late,available=late)
        with self.assertRaisesRegex(Refusal,'NOT_VISIBLE'): validate(controlled)
        with self.assertRaisesRegex(Refusal,'availability_mismatch'): validate(replace(controlled,available=OLD))
        self.assertEqual(validate(replace(controlled,controlled=False,available=OLD)),D(30))
    def test_q4_and_equivalent_bridge(self):
        decision=datetime(2027,2,1,tzinfo=timezone.utc)
        q3=fact(90,self.calendar[0],self.calendar[3])
        annual=fact(130,self.calendar[0],self.calendar[4])
        result=quarter(annual,q3,boundaries=self.calendar,ytd_attested=True,decision=decision)
        self.assertEqual((result.start,result.amount),(date(2026,10,1),D(40)))
        prior_fy, current_ytd, prior_ytd = D(100), D(90), D(80)
        self.assertEqual(prior_fy+current_ytd-prior_ytd,D(110))
    def test_first_quarter_negative_capex_and_future_period(self):
        with self.assertRaises(Refusal): self.construct(replace(self.q1,concept='CAPEX',amount=D(-1)))
        with self.assertRaises(Refusal): validate(replace(self.q1,end=date(2027,3,31)))
    def test_noncalendar_explicit_quarters(self):
        # A documented 53-week fiscal year: actual boundaries, never day-count inference.
        calendar=(date(2025,2,2),date(2025,5,3),date(2025,8,2),date(2025,11,1),date(2026,2,7))
        previous=fact(90,calendar[0],calendar[3])
        annual=fact(130,calendar[0],calendar[4])
        q4=quarter(annual,previous,boundaries=calendar,ytd_attested=True)
        self.assertEqual((q4.start,q4.end,q4.amount),(date(2025,11,2),date(2026,2,7),D(40)))
    def test_residual_late_component_and_debt_scope(self):
        later=datetime(2026,10,9,tzinfo=timezone.utc)
        with self.assertRaises(Refusal):
            cash_residual(fact(65),[replace(fact(15),retrieved=later,available=later)],exhaustive=True)
        with self.assertRaises(Refusal):
            debt([fact(12),fact(80,scope='segment')],disjoint=True,exhaustive=True)
    def test_cash_complete_residual(self):
        combined=fact(65); parts=[fact(10),fact(5)]
        self.assertEqual(cash_residual(combined,parts,exhaustive=True),D(50))
        with self.assertRaises(Refusal): cash_residual(combined,parts[:1],exhaustive=False)
        with self.assertRaises(Refusal): cash_residual(fact(4),parts,exhaustive=True)
        with self.assertRaises(Refusal): cash_residual(combined,[replace(parts[0],end=date(2026,3,31))],exhaustive=True)

if __name__ == '__main__': unittest.main(verbosity=2)
