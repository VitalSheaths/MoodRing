"""Retrospective comparison contracts and synthetic-testable safeguards.

This module exposes no forecasting training or scoring runner. Strict PIT
readiness remains independent; retrospective inputs cannot acquire PIT status.
"""
import math
import numpy as np
import pandas as pd

from .archive import digest, json_bytes
from .research_experiment import SPLITS, TrainOnlyStandardizer
from .sentiment_inventory import partition
from .sentiment_policy import POLICY
from .point_in_time import sessions_between

ARMS = tuple(POLICY['arms'])
FAMILIES = ('historical_variance_20','regularized_regression','boosted_trees','small_master')
PROTOCOL = {
    'version':'retrospective-comparison-protocol-v1',
    'scope':'retained retrospectively vendor-adjusted prices; not exact historical replay',
    'splits':SPLITS, 'context_sessions':8, 'horizon_sessions':5,
    'partition_context':'first seven sessions of each partition excluded; no cross-partition observation votes',
    'technical_history':'trailing indicator computation may use earlier price history; no fitted preprocessing crosses training boundary',
    'population':'existing price_target_candidate AND eight context sessions wholly in partition AND no known gap in context',
    'population_change':'materialized comparison subset only; original population unchanged; never relax technical warmup',
    'missing_channels':'retain rows and nulls; masks and counts; no neutral/forward fill',
    'target':'252/5 * sum of next five vendor-adjusted daily log returns squared; no demeaning',
    'outcome_availability':'complete consistently adjusted anchor plus five sessions, mature by label_asof; not by prediction cutoff',
    'boundary_purge':'all five future sessions inside the same split; right-censored labels excluded',
    'benchmark':'252/20 * sum of last twenty squared vendor-adjusted log returns, including anchor',
    'forecast_floor':1e-8,'extremes':'no outcome clipping, winsorization or extreme-label removal',
    'primary_metric':'mean QLIKE = log(predicted annualized variance) + realized annualized variance / predicted variance',
    'secondary_metrics':['MAE of annualized volatility','RMSE of annualized volatility'],
    'arms':ARMS,'families':FAMILIES,
    'family_fit_contracts':{
        'historical_variance_20':'no fitting; same predictions in every arm; reference rather than a sentiment learner',
        'regularized_regression':'ridge on annualized variance with intercept; MSE plus L2 penalty; floor predictions only',
        'boosted_trees':'squared-error boosting on annualized variance; floor predictions only',
        'small_master':'positive softplus variance plus floor; QLIKE loss on annualized variance; label mask separate from attention mask'},
    'preprocessing':'fit once on unique training feature date/ticker observations; training mean imputation and scaling; append masks',
    'all_missing_training_columns':'omit using training only; record dropped names; common columns identical across arms',
    'model_selection':'same predeclared budget per learned family/arm on dev only; no selection yet; simple families before transformer',
    'seeds':[17,29,43],
    'candidate_grids':{'regularized_regression':{'alpha':[0.1,1,10,100]},
        'boosted_trees':{'max_leaf_nodes':[7,15],'learning_rate':[0.03,0.1],'max_iter':[100,200]},
        'small_master':{'hidden_size':[16,32],'learning_rate':[0.0001,0.001],'dropout':[0.1,0.2],
                        'max_epochs':50,'dev_patience':5}},
    'uncertainty':'paired date means, all stocks together; 20-session moving blocks within contiguous segments; 2000 replicates, seed 17',
    'uncertainty_sensitivity_blocks':[5,10,40],
    'short_segments':'no interval if any contiguous segment has fewer than the chosen block length; never bridge source gaps',
    'test_use':'mechanical integrity only until configurations frozen and explicit evaluation release',
    'coverage_sensitivities':['all eight days observed','direct and indirect all eight days; identical subset across arms'],
    'timing_sensitivities':{'macro_extra_session_lags':[0,1,5,20],'news_extra_session_lags':[0,1,5]},
    'limitations':['current adjusted snapshot, not historical price versions',
        'macro vintage-date timing bounds are assumptions, not channel receipts',
        'news observation proxy, not exact historically available title version',
        '2025 source gap retained; fixed current ten-stock universe has survivorship limitations'],
    'full_scoring_held':True,'training_held':True,'strict_PIT_eligible':False,
}


def exclusion_ledger(features, population, technical_columns):
    """Explain existing exclusions without modifying eligibility or looking at labels.

    Missing anchor features and missing features in preceding context slots are
    separate causes. Macro nulls are diagnostic, not added exclusion requirements.
    """
    rows=[]
    population=population.set_index(['ticker','session'],verify_integrity=True)
    macro=[c for c in features if c.startswith('macro_')]
    for ticker,group in features.sort_values(['ticker','session']).groupby('ticker',sort=True):
        records=group.to_dict('records')
        for i,row in enumerate(records):
            existing=population.loc[(ticker,row['session'])]
            if existing['status']!='mature_retrospective' or existing['price_target_candidate']:
                continue
            window=records[max(0,i-7):i+1]
            missing=[c for c in technical_columns if pd.isna(row[c])]
            causes=[dict(session=r['session'],missing_features=[c for c in technical_columns if pd.isna(r[c])])
                    for r in window if any(pd.isna(r[c]) for c in technical_columns)]
            complete=len(window)==8 and not causes
            if complete!=bool(row['eight_session_features_complete']):
                raise ValueError('context_flag_not_reproducible')
            reasons=[]
            if missing:reasons.append('anchor_technical_warmup')
            if len(window)<8:reasons.append('fewer_than_eight_context_sessions')
            if any(r['session']!=row['session'] for r in causes):reasons.append('prior_context_technical_warmup')
            if existing['price_history_review_required']:reasons.append('price_history_adjustment_hold')
            if existing['adjustment_review_required']:reasons.append('outcome_adjustment_hold')
            if not reasons:raise ValueError('unexplained_exclusion')
            rows.append(dict(ticker=ticker,session=row['session'],split=partition(row['session']),
                missing_anchor_features=missing,context_missing_features=causes,
                missing_context_slots=max(0,8-len(window)),reasons=reasons,
                exclusive_cause='anchor_technical_warmup' if missing else 'context_only_warmup',
                macro_missing_not_an_exclusion=[c for c in macro if pd.isna(row[c])],
                unchanged_price_target_candidate=False,intended_by_existing_complete_feature_contract=True))
    return rows


def population_contract(population):
    """Apply dev's context/gap rules to every split, using metadata only."""
    if population.duplicated(['ticker','session']).any():raise ValueError('duplicate_population_key')
    result=[]
    for ticker,group in population.sort_values(['ticker','session']).groupby('ticker'):
        for split,start,end in SPLITS:
            records=group[(group.session>=start)&(group.session<end)].to_dict('records')
            if not records:continue
            expected=[d for d,_ in sessions_between(records[0]['session'],records[-1]['session'])]
            if [r['session'] for r in records]!=expected:raise ValueError('population_session_gap')
            for i,row in enumerate(records):
                window=records[max(0,i-7):i+1]
                complete=len(window)==8
                gap=any(r['known_source_gap_window'] for r in window)
                eligible=bool(row['price_target_candidate'] and complete and not gap)
                result.append(dict(ticker=ticker,session=row['session'],split=split,
                    row_id=digest(json_bytes([ticker,row['session']])),
                    price_target_candidate=bool(row['price_target_candidate']),
                    partition_context_complete=complete,known_gap_in_context=gap,
                    primary_matched=eligible,context_sessions=[r['session'] for r in window],
                    reasons=([row['status']] if row['status']!='mature_retrospective' else [])+
                        ([] if row['price_target_candidate'] else ['existing_price_target_exclusion'])+
                        ([] if complete else ['partition_context_boundary'])+(['known_source_gap_context'] if gap else []),
                    arms=list(ARMS),released_for_training=False,historical_prediction_eligible=False))
    return result


def feature_arms(baseline,coverage,direct,indirect):
    """Explicit feature allowlists; no targets, predictions or tone in coverage."""
    blocks=[list(x) for x in (baseline,coverage,direct,indirect)]
    flat=sum(blocks,[])
    if len(flat)!=len(set(flat)):raise ValueError('overlapping_feature_blocks')
    if any(any(word in c for word in ('label','target','annualized_variance','prediction')) for c in flat):
        raise ValueError('outcome_in_feature_contract')
    if any('sentiment' in c or 'probability' in c or 'tone' in c for c in coverage):
        raise ValueError('tone_in_coverage_controls')
    b,c,d,i=blocks
    return dict(zip(ARMS,(b,b+c,b+c+d,b+c+i,b+c+d+i)))


class FrozenPreprocessor:
    """Drop all-missing columns using training only, preserving all other masks."""
    def fit(self,values,cutoffs,keys,names):
        if hasattr(self,'scaler'):raise ValueError('preprocessing_already_frozen')
        x=np.asarray(values,dtype=float)
        if x.ndim!=2 or len(names)!=x.shape[1] or len(set(names))!=len(names):raise ValueError('feature_names')
        if len(keys)!=len(x) or len(set(keys))!=len(keys):raise ValueError('unique_training_observations_required')
        if len(cutoffs)!=len(x) or not len(x) or any(partition(t)!='train' for t in cutoffs):raise ValueError('train_only_fit')
        self.keep=~np.isnan(x).all(axis=0)
        if not self.keep.any():raise ValueError('no_training_features')
        self.dropped=[n for n,k in zip(names,self.keep) if not k]
        self.scaler=TrainOnlyStandardizer().fit(x[:,self.keep],cutoffs)
        return self
    def transform(self,values):
        x=np.asarray(values,dtype=float)
        if x.ndim!=2 or x.shape[1]!=len(self.keep):raise ValueError('feature_shape')
        return self.scaler.transform(x[:,self.keep])


def validate_predictions(population_keys,arms):
    """Require exact ordered keys for every family/arm; never silently intersect."""
    if len(population_keys)!=len(set(population_keys)):raise ValueError('duplicate_population_key')
    expected={(f,a) for f in FAMILIES for a in ARMS}
    if set(arms)!=expected:raise ValueError('missing_family_or_arm')
    for name,(keys,values) in arms.items():
        if list(keys)!=list(population_keys):raise ValueError('unmatched_prediction_rows')
        x=np.asarray(values,dtype=float)
        if x.shape!=(len(population_keys),) or not np.isfinite(x).all() or (x<=0).any():raise ValueError('positive_variance_required')
    reference=arms[('historical_variance_20',ARMS[0])][1]
    if any(not np.array_equal(reference,arms[('historical_variance_20',a)][1]) for a in ARMS[1:]):
        raise ValueError('historical_benchmark_must_not_depend_on_sentiment_arm')


def segmented_date_interval(dates,tickers,loss_a,loss_b,block_sessions=20,repeats=2000,seed=17):
    """Paired date bootstrap respecting missing-date gaps and overlapping targets."""
    a=np.asarray(loss_a,float);b=np.asarray(loss_b,float)
    if a.ndim!=1 or a.shape!=b.shape or len(a)!=len(dates) or len(a)!=len(tickers) or not len(a):raise ValueError('paired_shape')
    if len(set(zip(dates,tickers)))!=len(dates) or not np.isfinite(a-b).all():raise ValueError('paired_keys_or_values')
    if type(block_sessions) is not int or block_sessions<5 or type(repeats) is not int or not 100<=repeats<=100000:raise ValueError('bootstrap_bound')
    unique=sorted(set(dates));calendar=[d for d,_ in sessions_between(unique[0],unique[-1])]
    positions={d:i for i,d in enumerate(calendar)}
    if any(d not in positions for d in unique):raise ValueError('non_session_date')
    segments=[[]]
    for d in unique:
        if segments[-1] and positions[d]!=positions[segments[-1][-1]]+1:segments.append([])
        segments[-1].append(d)
    if len(unique)<2*block_sessions or any(len(s)<block_sessions for s in segments):raise ValueError('insufficient_contiguous_date_blocks')
    differences=a-b;means={d:float(differences[np.asarray(dates)==d].mean()) for d in unique}
    rng=np.random.default_rng(seed);samples=[]
    arrays=[np.array([means[d] for d in s]) for s in segments]
    for _ in range(repeats):
        draws=[]
        for arr in arrays:
            starts=rng.integers(0,len(arr)-block_sessions+1,size=math.ceil(len(arr)/block_sessions))
            draws.extend(np.concatenate([arr[s:s+block_sessions] for s in starts])[:len(arr)])
        samples.append(np.mean(draws))
    low,high=np.quantile(samples,[.025,.975])
    return dict(mean_paired_difference=float(np.mean(list(means.values()))),lower_95=float(low),upper_95=float(high),
        segment_lengths=[len(s) for s in segments],block_sessions=block_sessions,repeats=repeats,seed=seed,
        prediction_dates=len(unique),stock_rows=len(a),gap_bridging=False)
