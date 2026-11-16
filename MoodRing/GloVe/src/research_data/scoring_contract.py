"""Paused local scoring specification and checkpoint validation; no inference runner."""
import math
from .archive import digest,json_bytes

CONTRACT={
    'version':'generic-headline-local-canary-v1','status':'proposed_paused',
    'purpose':'operational_canary_not_panel_scores',
    'scope':'up to 5000 exact inputs in matched development contexts; exclude every prior benchmark input',
    'selection':'ascending exact UTF-8 SHA256, no score or target based selection',
    'split':'dev','max_unique_inputs':5000,'max_model_forward_attempts':5500,
    'max_attempts_per_input':2,'batch_size':16,'checkpoint_unique_inputs':250,
    'device':'cpu','threads':2,'precision':'float32','eval_mode':True,
    'max_tokens':512,'truncation':False,'remote_code':False,'network':False,
    'tone':'generic_headline','company_attribution':'separate upstream reviewed observation routing',
    'competitor_sign_inversion':False,'missing':'null plus reason; never neutral or forward-filled',
    'benchmark_outputs':'read-only diagnostic reference; no imports into canary or panel cache',
    'cache_identity':'purpose + model revision/file hashes + tokenizer hashes + package/inference contract + exact text hash',
    'mapping':'cache hits never merge sources or alter company/session/availability',
    'checkpoint':'write immutable 250-input shards atomically, manifest last; verify source and output hashes on resume',
    'attempt_ledger':'durable write-ahead attempt reservation before each forward batch; crash-uncertain attempts count against budget',
    'resume':'revalidate every binding; no replacement snapshot; reuse verified results; at most one counted retry per input',
    'stop_on':'changed binding, hold violation, unexpected token status, invalid probabilities, mismatch, or exhausted attempt budget',
    'aggregation':'not released; partial canary outputs cannot populate panel sentiment',
    'review_gate':'human dev adjudication and explicit bounded-run approval required',
    'full_scoring_held':True,'forecasting_training_held':True,'paid_calls_allowed':False,
}


def cache_namespace(model_contract):
    """Separate canary and benchmark namespaces even with identical model weights."""
    if not model_contract or not isinstance(model_contract,dict):raise ValueError('model_contract_required')
    return digest(json_bytes(dict(purpose=CONTRACT['purpose'],scoring_contract=CONTRACT,model=model_contract)))


def validate_checkpoint(manifest,rows,expected_ids,run_id):
    """Fail closed on changed scope, duplicate/missing outputs or invalid scores.

    This validates supplied data only. It neither runs inference nor grants release.
    Terminal failures preserve null probabilities; every attempted input is accounted.
    """
    if not 0<len(expected_ids)<=250 or len(set(expected_ids))!=len(expected_ids):raise ValueError('checkpoint_scope')
    if manifest.get('run_id')!=run_id or manifest.get('contract_sha256')!=digest(json_bytes(CONTRACT)):
        raise ValueError('checkpoint_binding')
    if manifest.get('input_ids_sha256')!=digest(json_bytes(expected_ids)) or manifest.get('rows_sha256')!=digest(json_bytes(rows)):
        raise ValueError('checkpoint_hash')
    if [r['exact_model_input_sha256'] for r in rows]!=expected_ids:raise ValueError('checkpoint_input_reconciliation')
    for r in rows:
        if r.get('status') not in ('scored','failed') or type(r.get('attempts')) is not int or not 1<=r['attempts']<=2:
            raise ValueError('checkpoint_state')
        if r['status']=='failed':
            if r.get('probabilities') is not None or r.get('headline_sentiment') is not None or not r.get('failure_reason'):
                raise ValueError('failure_must_stay_missing')
            continue
        probs=r.get('probabilities')
        if not isinstance(probs,dict) or set(probs)!={'positive','negative','neutral'} or any(
            isinstance(v,bool) or not isinstance(v,(int,float)) or not math.isfinite(v) or not 0<=v<=1 for v in probs.values()) or abs(sum(probs.values())-1)>1e-5:
            raise ValueError('invalid_probabilities')
        tone=r.get('headline_sentiment')
        if isinstance(tone,bool) or not isinstance(tone,(int,float)) or not math.isfinite(tone) or abs(tone-(probs['positive']-probs['negative']))>1e-6:
            raise ValueError('tone_probability_mismatch')
    if type(manifest.get('cumulative_forward_attempts')) is not int or not sum(r['attempts'] for r in rows)<=manifest['cumulative_forward_attempts']<=5500:
        raise ValueError('attempt_budget')
    return dict(inputs=len(rows),scored=sum(r['status']=='scored' for r in rows),failed=sum(r['status']=='failed' for r in rows),scoring_released=False)
