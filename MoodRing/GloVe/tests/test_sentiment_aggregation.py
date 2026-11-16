"""Synthetic aggregation, timing and release gates; no text model or real panel."""
from copy import deepcopy
from datetime import timedelta

import pytest

from research_data.archive import digest, json_bytes, verified_tables
from research_data.point_in_time import sessions_between
from research_data.sentiment_aggregation import build_aggregation, publish_aggregation, framed_rows_sha256
from research_data.retrospective_news import CHANNELS

POLICY = 'a' * 64
CONTRACT = 'b' * 64
DAYS = [d for d, _ in sessions_between('2024-01-02', '2024-01-11')]
CLOSES = dict(sessions_between(DAYS[0], DAYS[-1]))


def context(ticker='AAPL'):
    return dict(ticker=ticker, session=DAYS[-1], split='dev',
                row_id=digest(json_bytes([ticker, DAYS[-1]])), primary_matched=True,
                known_gap_in_context=False, context_sessions=DAYS[:])


def observation(text='one', serial=0, day=None, channel='direct', ticker='AAPL', n=1):
    day = day or DAYS[0]
    return dict(ticker=ticker, session=day, split='dev', sentiment_channel=channel,
                source_record_sha256=digest(str(serial).encode()), source_manifest_sha256='c'*64,
                source_manifest_row=serial, availability_at=(CLOSES[day]-timedelta(minutes=30)).isoformat(),
                cutoff=(CLOSES[day]+timedelta(minutes=20)).isoformat(),
                exact_model_input_sha256=digest(text.encode()), token_count=7, token_state='unscored',
                policy_sha256=POLICY, policy_observation_eligible=True, daily_input_occurrences=n,
                daily_input_weight=1/n, sentiment=None, panel_scored=False)


def score(text='one', positive=.8, negative=.1, neutral=.1):
    return digest(text.encode()), dict(status='scored', probabilities=dict(positive=positive, negative=negative, neutral=neutral),
        headline_sentiment=positive-negative, inference_contract_sha256=CONTRACT)


def build(rows, scores=None, contexts=None):
    contexts = contexts or [context()]
    return build_aggregation(rows, scores or {}, contexts, policy_sha256=POLICY,
                             inference_contract_sha256=CONTRACT,
                             expected_row_ids=[r['row_id'] for r in contexts])


def daily(result, day=None, channel='direct', ticker='AAPL'):
    return next(r for r in result['daily'] if r['session']==(day or DAYS[0])
                and r['channel']==channel and r['ticker']==ticker)


def approval(result):
    return dict(granted=True, scope='train_dev_aggregation_only', run_id=result['run_id'],
                approved_by='Synthetic fixture', approved_at='2026-11-14T00:00:00Z')


def test_streamed_hash_is_ordered_framed_and_content_sensitive():
    rows=[dict(a=1,b=2),dict(a=3)]
    assert framed_rows_sha256(iter(rows))==framed_rows_sha256(rows)
    assert framed_rows_sha256([dict(b=2,a=1),dict(a=3)])==framed_rows_sha256(rows)
    assert framed_rows_sha256(rows)!=framed_rows_sha256(list(reversed(rows)))
    assert framed_rows_sha256(['ab','c'])!=framed_rows_sha256(['a','bc'])
    assert framed_rows_sha256([])!=framed_rows_sha256([None])
    assert framed_rows_sha256(rows)!=framed_rows_sha256([dict(a=1,b=2),dict(a=4)])
    with pytest.raises(ValueError,match='sequence_bound'):framed_rows_sha256(iter(rows),max_rows=1)
    with pytest.raises(ValueError,match='record_bound'):framed_rows_sha256(['x'*(1024*1024)])


def test_daily_unique_weighting_preserves_all_source_records():
    rows=[observation(n=2),observation(serial=1,n=2),observation('two',serial=2)]
    before=deepcopy(rows)
    result=build(rows,dict([score(),score('two',positive=.1,negative=.8)]))
    d=daily(result)
    assert d['sentiment']==pytest.approx(0)
    assert d['observation_count']==3 and d['unique_input_count']==2
    assert result['audit']['daily_group_weight_sum']==2
    assert rows==before


def test_partial_scores_missing_and_neutral_remain_separate():
    rows=[observation(),observation('two',serial=1),observation('long',serial=2),
          observation('empty',serial=3),observation('failed',serial=4)]
    rows[2].update(token_count=513,token_state='overlength')
    rows[3].update(token_count=0,token_state='missing_text')
    scores=dict([score(positive=.1,negative=.1,neutral=.8)])
    scores[digest(b'failed')]=dict(status='failed',probabilities=None,headline_sentiment=None,
                                 failure_reason='two_attempts',inference_contract_sha256=CONTRACT)
    result=build(rows,scores);d=daily(result)
    assert d['sentiment']==0 and d['scored_inputs']==1 and d['zero_tone_input_count']==1
    assert d['neutral_label_input_count']==1
    for state in ('unscored','overlength','missing_text','failed'):assert d[state+'_inputs']==1
    assert d['missing_input_count']==4
    absent=daily(result,day=DAYS[1])
    assert absent['sentiment'] is None and absent['absent_news'] and not absent['observed']
    c=result['contexts'][0]
    assert c['direct_sentiment']==0 and c['direct_observed_days']==1 and c['direct_scored_days']==1
    assert c['direct_absent_news_days']==7 and c['direct_missing_score_days']==7
    assert not result['audit']['all_eligible_scores_successful']


def test_context_equal_day_means_not_observation_or_input_weighting():
    rows=[observation('a'),observation('b',serial=1),observation('c',serial=2,day=DAYS[1])]
    result=build(rows,dict([score('a',1,0,0),score('b',1,0,0),score('c',0,1,0)]))
    assert result['contexts'][0]['direct_sentiment']==0
    assert result['contexts'][0]['direct_scored_daily_inputs']==3


def test_channels_and_company_observations_do_not_merge_or_invert():
    rows=[observation('same',serial=i,channel=c) for i,c in enumerate(CHANNELS)]
    rows.append(observation('same',serial=3,ticker='MSFT'))
    result=build(rows,dict([score('same',0,1,0)]),[context(),context('MSFT')])
    for channel in CHANNELS:assert daily(result,channel=channel)['sentiment']==-1
    assert daily(result,ticker='MSFT')['sentiment']==-1
    assert result['audit']['daily_exact_input_groups']==4 and result['audit']['unique_scored_inputs']==1


@pytest.mark.parametrize('change,match',[
    ({'daily_input_occurrences':2},'daily_weights'),
    ({'daily_input_weight':.5},'daily_weights'),
    ({'daily_input_weight':float('nan')},'daily_weights'),
    ({'review_revision_action':'quarantine'},'held_observation'),
    ({'availability_at':'2024-01-02T22:00:00Z'},'future_observation'),
    ({'availability_at':'2022-12-31T20:00:00Z'},'crosses_partition'),
    ({'cutoff':'2024-01-02T21:00:00Z'},'incorrect_session_cutoff'),
    ({'split':'test'},'train_dev_only'),
    ({'policy_sha256':'d'*64},'weighting_policy_mismatch'),
])
def test_changed_or_unsafe_observation_fails(change,match):
    r=observation();r.update(change)
    with pytest.raises(ValueError,match=match):build([r])


def test_duplicate_source_or_partial_duplicate_group_fails():
    r=observation()
    with pytest.raises(ValueError,match='duplicate_source'):build([r,dict(r)])
    r['daily_input_occurrences']=2;r['daily_input_weight']=.5
    with pytest.raises(ValueError,match='daily_weights'):build([r])


def test_ineligible_sources_retained_in_audit_but_never_vote():
    r=observation();r.update(policy_observation_eligible=False,review_revision_action='quarantine')
    result=build([r])
    assert result['audit']['source_observations']==1 and result['audit']['included_observations']==0
    assert result['audit']['excluded_observations']=={'policy_ineligible':1}
    assert all(d['sentiment'] is None for d in result['daily'])


def test_scores_cannot_override_token_eligibility_or_be_orphans():
    r=observation();r.update(token_count=513,token_state='overlength')
    with pytest.raises(ValueError,match='token_ineligible'):build([r],dict([score()]))
    with pytest.raises(ValueError,match='orphan'):build([observation()],dict([score('other')]))


@pytest.mark.parametrize('change',[
    {'inference_contract_sha256':'e'*64},
    {'headline_sentiment':-.7},
    {'probabilities':{'positive':.8,'negative':.1,'neutral':.2}},
    {'status':'failed','probabilities':None,'headline_sentiment':0,'failure_reason':'failed'},
])
def test_changed_score_contract_or_invalid_value_fails(change):
    h,s=score();s.update(change)
    with pytest.raises(ValueError):build([observation()],{h:s})


def test_exact_matched_rows_and_eight_session_boundaries():
    c=context()
    with pytest.raises(ValueError,match='unmatched_context'):
        build_aggregation([],{},[c],policy_sha256=POLICY,inference_contract_sha256=CONTRACT,expected_row_ids=[])
    for change in ({'split':'test'},{'primary_matched':False},{'known_gap_in_context':True},
                   {'context_sessions':DAYS[:-1]},{'context_sessions':['2022-12-30']+DAYS[1:]}):
        bad=dict(c,**change)
        with pytest.raises(ValueError):build([],contexts=[bad])


def test_all_null_empty_news_retains_rows():
    result=build([])
    assert len(result['daily'])==24 and len(result['contexts'])==1
    assert result['contexts'][0]['direct_sentiment'] is None
    assert result['contexts'][0]['direct_absent_news_days']==8


def test_early_close_uses_exchange_cutoff_not_regular_hours():
    calendar=sessions_between('2024-11-19','2024-11-29')
    days=[d for d,_ in calendar][-8:];session=days[-1];close=dict(calendar)[session]
    c=dict(context(),session=session,context_sessions=days,row_id=digest(json_bytes(['AAPL',session])))
    r=observation();r.update(session=session,availability_at=(close-timedelta(minutes=10)).isoformat(),
                             cutoff=(close+timedelta(minutes=20)).isoformat())
    assert build([r],dict([score()]),[c])['contexts'][0]['direct_sentiment']==pytest.approx(.7)
    r['cutoff']=session+'T21:20:00Z'
    with pytest.raises(ValueError,match='incorrect_session_cutoff'):build([r],dict([score()]),[c])


def test_reordered_context_population_is_not_silently_intersected():
    contexts=[context(),context('MSFT')]
    with pytest.raises(ValueError,match='unmatched_context_rows'):
        build_aggregation([],{},list(reversed(contexts)),policy_sha256=POLICY,
            inference_contract_sha256=CONTRACT,expected_row_ids=[r['row_id'] for r in contexts])


def test_publication_requires_separate_exact_approval_and_complete_scores(tmp_path):
    result=build([observation()])
    for auth in (None,dict(granted=False),dict(approval(result),scope='isolated_pilot_only')):
        with pytest.raises(PermissionError):publish_aggregation(tmp_path/'denied',result,auth)
    with pytest.raises(PermissionError,match='scores_incomplete'):publish_aggregation(tmp_path/'denied',result,approval(result))
    assert not (tmp_path/'denied').exists()
    complete=build([observation()],dict([score()]))
    forged=deepcopy(complete);forged['contexts'][0]['direct_sentiment']=0
    with pytest.raises(ValueError,match='output_changed'):publish_aggregation(tmp_path/'denied',forged,approval(forged))
    manifest=publish_aggregation(tmp_path/'synthetic',complete,approval(complete))
    assert manifest['aggregation_released'] and not manifest['forecasting_training_released']
    _,tables=verified_tables(tmp_path/'synthetic')
    assert tables['contexts'].num_rows==1
    with pytest.raises(FileExistsError):publish_aggregation(tmp_path/'synthetic',complete,approval(complete))
