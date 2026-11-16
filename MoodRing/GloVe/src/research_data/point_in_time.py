"""Strict research reconstruction primitives; no provider calls or production writes.

Availability evidence must bind to the exact source version. A FRED vintage date,
a release calendar entry, or today's adjusted-price download is not such evidence.
The caller supplies reviewed evidence records; this module verifies their bindings,
not the truth of an external document. Existing diagnostic panels remain unchanged.
"""
from datetime import date, datetime, timedelta, timezone
import math
from .archive import digest, json_bytes
from .alignment import session_grid

POLICY_VERSION = 'pit-evidence-bound-v1'


class EvidenceLedger:
    """Resolve reviewed claims to checksum-verified supporting artifact bytes.

    Verification establishes retained document identity, not historical truth;
    a qualified reviewer must verify the exact-version availability assertion.
    """
    def __init__(self, records, artifacts):
        import json
        self._records = json.loads(json.dumps(records))
        for item in self._records.values():
            sha = item.get('supporting_artifact_sha256')
            if sha not in artifacts or digest(artifacts[sha]) != sha:
                raise ValueError('unresolved_evidence_artifact')

    def get(self, key):
        return self._records.get(key)



def utc(value):
    """Reject date-only and naive time values instead of guessing a timezone."""
    if not isinstance(value, (str, datetime)):
        raise ValueError('timestamp_required')
    stamp = datetime.fromisoformat(value.replace('Z', '+00:00')) if isinstance(value, str) else value
    if stamp.tzinfo is None or stamp.utcoffset() is None:
        raise ValueError('timezone_required')
    return stamp.astimezone(timezone.utc)


def source_id(record):
    """Hash the immutable source payload, excluding separately stored evidence links."""
    return digest(json_bytes({k:v for k,v in record.items() if k != 'evidence_id'}))


def available_at(record, evidence):
    """Resolve a reviewed exact-version availability fact or documented upper bound.

    Evidence retrieval may be later than the claim; an archive must demonstrate
    that this exact value/text/version already existed. Unknown remains unknown.
    """
    if not isinstance(evidence, EvidenceLedger):
        raise TypeError('verified_evidence_ledger_required')
    ref = record.get('evidence_id')
    if not ref:
        return None
    item = evidence.get(ref)
    if item is None or item.get('source_record_sha256') != source_id(record):
        raise ValueError('unresolved_or_mismatched_evidence')
    if (item.get('kind') not in ('observed_exact_version', 'archived_exact_version', 'documented_upper_bound')
            or item.get('reviewed') is not True or not item.get('source') or not item.get('supporting_artifact_sha256')):
        return None
    if item.get('kind') == 'documented_upper_bound' and not item.get('bound_justification'):
        return None
    stamp = utc(item['available_by'])
    if utc(item['evidence_retrieved_at']) < stamp:
        raise ValueError('evidence_predates_claim')
    return stamp


def sessions_between(start, end):
    """Handle a single requested session without widening the returned scope."""
    start=date.fromisoformat(start);end=date.fromisoformat(end)
    if start>end:raise ValueError('invalid_session_range')
    return [(s,c) for s,c in session_grid((start-timedelta(days=7)).isoformat(),(end+timedelta(days=7)).isoformat())
            if start.isoformat()<=s<=end.isoformat()]


def cutoffs(start, end, delay_minutes=20):
    """Fixed daily research cutoff after the actual exchange close, not execution.

    Twenty minutes is a proposed experiment policy, not proof prices were ready.
    Bars must separately pass their exact-version availability gate at that time.
    """
    if not isinstance(delay_minutes, int) or delay_minutes <= 0:
        raise ValueError('positive_after_close_delay_required')
    return [(s, close + timedelta(minutes=delay_minutes)) for s,close in sessions_between(start,end)]


def macro_asof(records, cutoff, evidence):
    """Select each period's latest eligible vintage; never overwrite earlier revisions.

    The returned full period history must be used to recompute rolling features
    separately at every cutoff. Null revisions mask prior values rather than fill.
    """
    cutoff = utc(cutoff); chosen = {}; seen = {}
    for row in records:
        period = date.fromisoformat(row['observation_date'])
        vintage = date.fromisoformat(row['realtime_start'])
        if period > vintage:
            continue
        stamp = available_at(row, evidence)
        if stamp is None or stamp > cutoff or vintage > stamp.date():
            continue
        value = row.get('value')
        if value is not None and not math.isfinite(float(value)):
            raise ValueError('nonfinite_macro')
        key = (row['series_id'], period); version = (*key,vintage)
        if version in seen and seen[version] != value:
            raise ValueError('conflicting_eligible_macro_vintage')
        seen[version] = value
        if key not in chosen or vintage > date.fromisoformat(chosen[key]['realtime_start']):
            chosen[key] = dict(row)
    return [chosen[k] for k in sorted(chosen)]


def macro_features(history, series):
    """Compute level, prior-period change and twelve-month change from one as-of view."""
    rows = sorted((r for r in history if r['series_id']==series),key=lambda r:r['observation_date'])
    result = dict(value=None, change=None, year_on_year_fraction=None, source_ids=[])
    if not rows:
        return result
    latest = rows[-1]; value = latest.get('value')
    result.update(value=value, source_ids=[source_id(latest)])
    if value is None:
        return result
    if len(rows)>1 and rows[-2].get('value') is not None:
        result['change'] = float(value)-float(rows[-2]['value']); result['source_ids'].append(source_id(rows[-2]))
    if series in ('CPIAUCSL','PAYEMS'):
        d=date.fromisoformat(latest['observation_date'])
        try: prior=d.replace(year=d.year-1).isoformat()
        except ValueError: prior=None
        base=next((r for r in rows if r['observation_date']==prior),None)
        if base and base.get('value') is not None and float(base['value'])>0:
            result['year_on_year_fraction']=float(value)/float(base['value'])-1
            result['source_ids'].append(source_id(base))
    return result


def latest_versions(records, cutoff, evidence, key_fields):
    """Select append-only versions by evidenced availability; reject conflicting ties."""
    cutoff=utc(cutoff); chosen={}
    for row in records:
        stamp=available_at(row,evidence)
        if stamp is None or stamp>cutoff:
            continue
        key=tuple(row[k] for k in key_fields)
        previous=chosen.get(key)
        if previous and previous[0]==stamp and source_id(previous[1])!=source_id(row):
            raise ValueError('conflicting_versions_at_same_time')
        if not previous or stamp>previous[0]:chosen[key]=(stamp,row)
    return [dict(chosen[k][1]) for k in sorted(chosen)]


def price_history(bars, actions, ledger_coverage, cutoff, evidence):
    """Derive raw, split-adjusted and dividend-inclusive returns for one issuer.

    Requires evidenced raw versions and a complete corporate-action ledger for the
    requested interval. Split ratios are new shares per old share. Dividends must
    be expressed per post-action share on their effective (ex-dividend) session.
    Cash is included at ex-date for a research total-return series, not a cash-flow
    execution simulation. All values stay missing if ledger coverage is unproven.
    """
    cutoff=utc(cutoff)
    rows=latest_versions(bars,cutoff,evidence,('ticker','session'))
    if not rows:return []
    if len({r['ticker'] for r in rows})!=1:raise ValueError('one_issuer_required')
    ticker=rows[0]['ticker']
    selected=[]
    for row in rows:
        if row.get('adjustment_basis')!='as_traded':raise ValueError('as_traded_prices_required')
        close_time=utc(row['session_close'])
        if close_time>cutoff:continue
        if available_at(row,evidence)<close_time:raise ValueError('bar_available_before_close')
        values=[float(row[k]) for k in ('open','high','low','close','volume')]
        o,h,l,c,v=values
        if not all(math.isfinite(x) for x in values) or min(o,h,l,c)<=0 or v<0 or not l<=min(o,c)<=max(o,c)<=h:
            raise ValueError('invalid_ohlcv')
        selected.append(row)
    if not selected:return []
    expected=dict(sessions_between(selected[0]['session'],selected[-1]['session']))
    if set(expected)!={r['session'] for r in selected}:raise ValueError('price_session_gap')
    if any(utc(r['session_close'])!=expected[r['session']] for r in selected):raise ValueError('exchange_close_mismatch')
    coverage_time=available_at(ledger_coverage,evidence)
    if (coverage_time is None or coverage_time>cutoff or ledger_coverage.get('complete') is not True or
            ledger_coverage.get('ticker')!=ticker or ledger_coverage['start']>selected[0]['session'] or
            ledger_coverage['end']<selected[-1]['session']):
        return []
    # A claimed complete ledger cannot contain unresolved effective action records.
    for a in actions:
        if a['ticker']==ticker and selected[0]['session']<=a['effective_session']<=selected[-1]['session'] and available_at(a,evidence) is None:
            raise ValueError('unresolved_action_in_complete_ledger')
    eligible=latest_versions(actions,cutoff,evidence,('ticker','action_id'))
    eligible=[a for a in eligible if a['ticker']==ticker and selected[0]['session']<=a['effective_session']<=selected[-1]['session'] and not a.get('cancelled')]
    by_day={}
    for a in eligible:
        if a['effective_session'] not in expected:raise ValueError('action_on_non_session')
        if a['kind'] not in ('split','dividend'):raise ValueError('unsupported_corporate_action')
        if a['kind']=='dividend' and a.get('share_basis')!='post_action_share':raise ValueError('dividend_units_required')
        value=float(a['value'])
        if not math.isfinite(value) or (a['kind']=='split' and value<=0) or (a['kind']=='dividend' and value<0):raise ValueError('invalid_action_value')
        by_day.setdefault(a['effective_session'],[]).append(a)
    result=[];previous=None
    for row in selected:
        future=[a for a in eligible if a['kind']=='split' and a['effective_session']>row['session']]
        factor=math.prod(float(a['value']) for a in future)
        item=dict(row,split_factor=factor,split_adjusted_volume=float(row['volume'])*factor,
                  action_source_ids=[source_id(a) for a in future],raw_simple_return=None,
                  split_simple_return=None,split_log_return=None,total_return=None,
                  split_log_price=math.log(float(row['close'])/factor))
        for field in ('open','high','low','close'):item['split_adjusted_'+field]=float(row[field])/factor
        if previous:
            item['raw_simple_return']=float(row['close'])/float(previous['close'])-1
            item['split_simple_return']=item['split_adjusted_close']/previous['split_adjusted_close']-1
            item['split_log_return']=math.log1p(item['split_simple_return'])
            today=by_day.get(row['session'],[])
            ratio=math.prod(float(a['value']) for a in today if a['kind']=='split')
            dividend=sum(float(a['value']) for a in today if a['kind']=='dividend')
            item['total_return']=(float(row['close'])+dividend)*ratio/float(previous['close'])-1
            item['return_action_source_ids']=[source_id(a) for a in today]
        else:item['return_action_source_ids']=[]
        result.append(item);previous=item
    return result
