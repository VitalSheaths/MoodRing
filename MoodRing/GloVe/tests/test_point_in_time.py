"""Release/revision boundaries and action evidence must prevent hindsight leakage."""
from copy import deepcopy
import pytest
from research_data import point_in_time as p


def bind(records, stamps, kinds=None):
    artifact=b'synthetic test evidence, not real availability approval'
    sha=p.digest(artifact);evidence={}
    for i,(row,stamp) in enumerate(zip(records,stamps)):
        row['evidence_id']=str(i)
        evidence[str(i)]=dict(source_record_sha256=p.source_id(row),kind=(kinds or {}).get(i,'observed_exact_version'),
            source='synthetic_fixture',supporting_artifact_sha256=sha,reviewed=True,
            available_by=stamp,evidence_retrieved_at='2026-11-09T00:00:00Z',bound_justification='test only')
    return p.EvidenceLedger(evidence,{sha:artifact})


def test_missing_unreviewed_and_mismatched_evidence():
    row=dict(series_id='CPIAUCSL',observation_date='2024-01-01',realtime_start='2024-02-13',value=10)
    assert p.macro_asof([row],'2024-03-01T21:20:00Z',p.EvidenceLedger({},{}))==[]
    e=bind([row],['2024-02-13T13:30:00Z'])
    assert p.available_at(row,e)==p.utc('2024-02-13T13:30:00Z')
    with pytest.raises(ValueError,match='mismatched'):p.available_at(dict(row,value=11),e)
    with pytest.raises(ValueError,match='timezone'):p.utc('2024-02-13')
    with pytest.raises(ValueError,match='unresolved_evidence_artifact'):p.EvidenceLedger({'x':{'supporting_artifact_sha256':'bad'}},{})


def test_releases_revisions_and_nulls_reconstruct_history():
    old=dict(series_id='CPIAUCSL',observation_date='2023-01-01',realtime_start='2023-02-14',value=100)
    current=dict(series_id='CPIAUCSL',observation_date='2024-01-01',realtime_start='2024-02-13',value=110)
    revision=dict(old,realtime_start='2024-02-20',value=105)
    missing=dict(current,realtime_start='2024-02-21',value=None)
    rows=[old,current,revision,missing]
    e=bind(rows,['2023-02-14T13:30:00Z','2024-02-13T13:30:00Z','2024-02-20T13:30:00Z','2024-02-21T13:30:00Z'])
    before=p.macro_asof(rows,'2024-02-13T13:29:59Z',e)
    assert len(before)==1
    at=p.macro_asof(rows,'2024-02-13T13:30:00Z',e)
    assert p.macro_features(at,'CPIAUCSL')['year_on_year_fraction']==pytest.approx(.1)
    later=p.macro_asof(rows,'2024-02-20T13:30:00Z',e)
    assert p.macro_features(later,'CPIAUCSL')['year_on_year_fraction']==pytest.approx(110/105-1)
    assert p.macro_features(p.macro_asof(rows,'2024-02-21T13:30:00Z',e),'CPIAUCSL')['value'] is None
    assert old['value']==100


def test_supported_later_bound_does_not_turn_into_release_timestamp():
    row=dict(series_id='UNRATE',observation_date='2024-01-01',realtime_start='2024-02-02',value=3.7)
    e=bind([row],['2024-02-03T05:00:00Z'],{0:'documented_upper_bound'})
    assert not p.macro_asof([row],'2024-02-02T21:20:00Z',e)
    assert len(p.macro_asof([row],'2024-02-03T05:00:00Z',e))==1


def test_early_close_and_dst_cutoffs():
    assert dict(p.cutoffs('2024-11-29','2024-11-29'))['2024-11-29']==p.utc('2024-11-29T18:20:00Z')
    cut=dict(p.cutoffs('2024-03-08','2024-03-11'))
    assert cut['2024-03-08']==p.utc('2024-03-08T21:20:00Z')
    assert cut['2024-03-11']==p.utc('2024-03-11T20:20:00Z')
    with pytest.raises(ValueError):p.cutoffs('2024-03-08','2024-03-11',0)


def prices_fixture():
    bars=[dict(ticker='TEST',session=s,session_close=s+'T21:00:00Z',adjustment_basis='as_traded',
               open=c,high=c+1,low=c-1,close=c,volume=100) for s,c in [('2024-01-02',100),('2024-01-03',50),('2024-01-04',49)]]
    split=dict(ticker='TEST',action_id='split',effective_session='2024-01-03',kind='split',value=2)
    dividend=dict(ticker='TEST',action_id='cash',effective_session='2024-01-04',kind='dividend',value=1,share_basis='post_action_share')
    cover=dict(ticker='TEST',start='2024-01-02',end='2024-01-04',complete=True)
    records=bars+[split,dividend,cover]
    e=bind(records,[r['session']+'T21:05:00Z' for r in bars]+['2024-01-02T14:00:00Z','2024-01-03T14:00:00Z','2024-01-04T21:05:00Z'])
    return bars,[split,dividend],cover,e


def test_split_dividend_and_raw_returns_stay_distinct():
    bars,actions,cover,e=prices_fixture();before=deepcopy(bars)
    result=p.price_history(bars,actions,cover,'2024-01-04T21:20:00Z',e)
    assert result[0]['split_adjusted_close']==50 and result[0]['split_adjusted_volume']==200
    assert result[1]['raw_simple_return']==-.5
    assert result[1]['split_simple_return']==0 and result[1]['split_log_return']==0
    assert result[2]['split_simple_return']==pytest.approx(-.02)
    assert result[2]['total_return']==0 and bars==before
    # The ledger was not evidenced at the earlier cutoff: fail closed.
    assert p.price_history(bars,actions,cover,'2024-01-03T21:20:00Z',e)==[]


def test_retroactively_adjusted_prices_and_gaps_are_rejected():
    bars,actions,cover,e=prices_fixture()
    with pytest.raises(ValueError,match='price_session_gap'):
        p.price_history([bars[0],bars[2]],actions,cover,'2024-01-04T21:20:00Z',e)
    bars[0]['adjustment_basis']='split_adjusted'
    e=bind(bars+actions+[cover],[r['session']+'T21:05:00Z' for r in bars]+['2024-01-02T14:00:00Z']*3)
    with pytest.raises(ValueError,match='as_traded'):
        p.price_history(bars,actions,cover,'2024-01-04T21:20:00Z',e)


def test_future_price_correction_is_not_applied_early():
    bars,actions,cover,_=prices_fixture()
    correction=dict(bars[0],close=100.5)
    rows=bars+[correction]+actions+[cover]
    e=bind(rows,[r['session']+'T21:05:00Z' for r in bars]+['2024-01-05T12:00:00Z']+['2024-01-02T14:00:00Z']*3)
    earlier=p.price_history(bars+[correction],actions,cover,'2024-01-04T21:20:00Z',e)
    later=p.price_history(bars+[correction],actions,cover,'2024-01-05T21:20:00Z',e)
    assert earlier[0]['close']==100 and later[0]['close']==100.5


def test_known_future_action_cannot_adjust_pre_effective_history():
    bars,actions,cover,_=prices_fixture()
    cover.update(end='2024-01-02')
    e=bind(bars+actions+[cover],[r['session']+'T21:05:00Z' for r in bars]+['2024-01-02T14:00:00Z']*3)
    result=p.price_history(bars,actions,cover,'2024-01-02T21:20:00Z',e)
    assert len(result)==1 and result[0]['split_adjusted_close']==100


def test_unknown_action_and_unsupported_effective_session_fail_closed():
    bars,actions,cover,e=prices_fixture()
    unknown=dict(actions[0]);unknown.pop('evidence_id')
    with pytest.raises(ValueError,match='unresolved_action'):
        p.price_history(bars,[unknown,actions[1]],cover,'2024-01-04T21:20:00Z',e)
    # A malformed close time must not bypass calendar validation.
    bars[0]['session_close']='2024-01-02T20:00:00Z'
    e=bind(bars+actions+[cover],[r['session']+'T21:05:00Z' for r in bars]+['2024-01-02T14:00:00Z']*3)
    with pytest.raises(ValueError,match='exchange_close_mismatch'):
        p.price_history(bars,actions,cover,'2024-01-04T21:20:00Z',e)



def test_string_false_cannot_approve_availability_or_ledger():
    row=dict(value=1,evidence_id='x')
    artifact=b'evidence';sha=p.digest(artifact)
    claim=dict(source_record_sha256=p.source_id(row),kind='observed_exact_version',source='fixture',
               supporting_artifact_sha256=sha,reviewed='false',available_by='2024-01-02T21:05:00Z',evidence_retrieved_at='2026-01-01T00:00:00Z')
    assert p.available_at(row,p.EvidenceLedger({'x':claim},{sha:artifact})) is None
    bars,actions,cover,_=prices_fixture();cover['complete']='false'
    e=bind(bars+actions+[cover],[r['session']+'T21:05:00Z' for r in bars]+['2024-01-02T14:00:00Z']*3)
    assert p.price_history(bars,actions,cover,'2024-01-04T21:20:00Z',e)==[]
