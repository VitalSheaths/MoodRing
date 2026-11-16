"""Synthetic fixtures only, executable with the retained local Torch environment."""
import unittest
from copy import deepcopy
from unittest.mock import patch
from pathlib import Path
from tempfile import TemporaryDirectory
import numpy as np
try:
    import torch
except ImportError:
    raise unittest.SkipTest('optional torch research environment required')
from research_data import positive_master_experiments as e
from research_data.master_model import masked_variance_loss
from test_forecast_master import batch
from research_data.forecast_master import prepare_master
from research_data.forecast_runners import PreparedSplit
from research_data.point_in_time import sessions_between


def prepared(start,end,split):
    schedule=sessions_between(start,end);tickers=('SYN1','SYN2')
    keys=tuple((d,t) for d,_ in schedule for t in tickers);lookup={k:i for i,k in enumerate(keys)}
    features=np.array([[i+j,i] for i,_ in enumerate(schedule) for j in range(2)],float)
    anchors=tuple((schedule[i][0],t) for i in range(7,len(schedule)-5) for t in tickers)
    positions={d:i for i,(d,_) in enumerate(schedule)}
    windows=np.array([[lookup[(schedule[j][0],t)] for j in range(positions[d]-7,positions[d]+1)] for d,t in anchors])
    return PreparedSplit(split,('price','macro_shared'),keys,tuple(c.isoformat() for _,c in schedule for t in tickers),
        features,windows,anchors,tuple(str(k) for k in anchors),np.ones(len(anchors))*.04,np.ones(len(anchors))*.04)


class PositiveExperimentTests(unittest.TestCase):
    def test_softplus_is_positive_but_saturation_stays_visible(self):
        model=e.PositiveRidge(2,.1)
        with torch.no_grad():model.bias.fill_(-1000)
        out=model(torch.zeros((3,2),dtype=torch.float64))
        self.assertTrue((out==1e-8).all())
        with torch.no_grad():model.bias.fill_(100)
        self.assertTrue(torch.isfinite(model(torch.ones((3,2),dtype=torch.float64))).all())

    def test_synthetic_mse_extreme_retained_and_deterministic(self):
        x=np.arange(40,dtype=float).reshape(20,2)/40;y=np.ones(20)*.04;y[-1]=100.
        cfg=dict(alpha=1.,learning_rate=.01,max_steps=5)
        r=e.fit_positive(x,y,x,cfg);s=e.fit_positive(x,y,x,cfg)
        self.assertEqual(r,s);self.assertEqual(y[-1],100.)
        self.assertEqual(r['extreme_targets_removed'],0);self.assertFalse(r['convergence_claim'])
        self.assertTrue((np.array(r['predicted_variance'])>0).all())

    def test_initialization_depends_only_on_training_target(self):
        x=np.ones((5,2));y=np.arange(5,dtype=float)
        a=e.fit_positive(x,y,x,dict(alpha=1.,learning_rate=.01,max_steps=2))
        b=e.fit_positive(x,y,x*1000,dict(alpha=1.,learning_rate=.01,max_steps=2))
        self.assertEqual(a['model_state'],b['model_state'])

    def test_no_approval_rejects_before_data_or_output(self):
        p=dict(run_id='fixture',identity=dict(scope=e.SCOPES[0]))
        with TemporaryDirectory() as d,patch.object(e,'plan',side_effect=AssertionError('should not load')):
            with self.assertRaises(PermissionError):e.execute(p,d,dict(granted=False))
            self.assertEqual(list(Path(d).iterdir()),[])

    def test_old_scope_and_changed_identity_cannot_release(self):
        p=dict(run_id='fixture',identity=dict(scope=e.SCOPES[1],descriptor={}))
        old=dict(granted=True,scope='matched_train_dev_sentiment_comparison',run_id='fixture',approved_by='synthetic')
        with self.assertRaises(PermissionError):e.execute(p,'.',old)
        approved=dict(granted=True,scope=e.SCOPES[1],run_id='fixture',approved_by='synthetic',approved_at='2026-11-15T00:00:00Z')
        with patch.object(e,'plan',return_value={'changed':True}):
            with self.assertRaises(ValueError):e.execute(p,'.',approved)

    def test_exact_model_grid_and_macro_gate_mapping(self):
        p=prepared('2021-01-04','2021-02-05','train');b=e.master_batch(p)
        self.assertEqual(b.x.shape,(len(p.keys)//2,2,8,2))
        np.testing.assert_array_equal(b.target.reshape(-1),p.target)
        np.testing.assert_array_equal(b.x.reshape(len(p.keys),8,2),p.features[p.window_indices])
        self.assertEqual(b.market_names,('macro_shared',))
        bad=deepcopy(p);bad.features[p.window_indices[0,-1],1]+=1
        with self.assertRaises(ValueError):e.master_batch(bad)
        bad=deepcopy(p);bad.keys=bad.keys[::-1]
        with self.assertRaises(ValueError):e.master_batch(bad)

    def test_test_partition_and_future_token_rejected(self):
        p=prepared('2023-01-03','2023-02-03','dev');bad=deepcopy(p);bad.split='test'
        with self.assertRaises(ValueError):e.master_batch(bad)
        bad=deepcopy(p);times=list(bad.feature_cutoffs);times[0]='2024-01-01T00:00:00Z';bad.feature_cutoffs=tuple(times)
        with self.assertRaises(ValueError):e.master_batch(bad)

    def test_reserved_interruption_and_no_automatic_resume(self):
        t=prepared('2021-01-04','2021-02-05','train');d=prepared('2023-01-03','2023-02-03','dev')
        extras={arm:(np.empty((len(t.keys),0)),np.empty((len(d.keys),0))) for arm in e.ARMS}
        with TemporaryDirectory() as root:
            root=Path(root).resolve()
            desc=dict(experiment_outputs={e.SCOPES[0]:'execution'})
            p=dict(run_id='synthetic',identity=dict(scope=e.SCOPES[0],descriptor=desc,max_fits=20,
                configs=e.RIDGE_CONFIGS,seeds=[17]))
            a=dict(granted=True,run_id='synthetic',scope=e.SCOPES[0],approved_by='synthetic',approved_at='2026-11-15T00:00:00Z')
            with patch.object(e,'plan',return_value=p),patch.object(e.matched,'load_comparison',return_value=(t,d,None,None,None,None)),patch.object(e,'anchor_blocks',return_value=(extras,{})),patch.object(e,'fit_positive',side_effect=KeyboardInterrupt) as fit:
                with self.assertRaises(KeyboardInterrupt):e.execute(p,root,a)
                self.assertEqual(fit.call_count,1)
                self.assertTrue((Path(root)/'execution/attempt-0000.json').exists())
                self.assertFalse((Path(root)/'execution/completion.json').exists())
                with self.assertRaises(FileExistsError):e.execute(p,root,a)
                self.assertEqual(fit.call_count,1)

    def test_fixed_and_tuned_comparisons_are_separate(self):
        configs=[{'alpha':1},{'alpha':10}];records=[]
        for arm in e.ARMS:
            for cfg in configs:
                value=(0 if cfg==configs[0] else 1) if arm==e.ARMS[0] else (1 if cfg==configs[0] else 0)
                records.append(dict(arm=arm,config=cfg,seed=17,metrics={'qlike':value}))
        with patch.object(e,'development_comparison',side_effect=lambda r,c,d,**kwargs:dict(reference_config=r['config'],candidate_config=c['config'])):
            report=e.summarize(records,None,configs,[17])
        self.assertEqual(report['fixed_new_baseline_configuration'],configs[0])
        name=e.ARMS[2]+'-vs-'+e.ARMS[1]
        fixed=report['per_seed_descriptive_intervals']['fixed_new_baseline_configuration'][name]['17']['20']
        tuned=report['per_seed_descriptive_intervals']['per_arm_development_tuning'][name]['17']['20']
        self.assertEqual(fixed['candidate_config'],configs[0]);self.assertEqual(tuned['candidate_config'],configs[1])


class AnchorMasterTests(unittest.TestCase):
    def setUp(self):
        torch.set_num_threads(2);torch.manual_seed(17)
        self.model=e.AnchorMaster(4,2,3,width=16,dropout=0,max_lookback=8).eval()
        self.x=torch.randn(2,3,8,4);self.market=torch.randn(2,2);self.anchor=torch.randn(2,3,3)
        self.mask=torch.ones(2,3,8,dtype=torch.bool)

    def test_anchor_changes_do_not_cross_prediction_dates(self):
        a=self.model(self.x,self.market,self.mask,self.anchor)
        changed=self.anchor.clone();changed[1]+=100
        b=self.model(self.x,self.market,self.mask,changed)
        torch.testing.assert_close(a[0],b[0]);self.assertFalse(torch.allclose(a[1],b[1]))

    def test_late_fusion_does_not_change_historical_representation(self):
        before=self.model.representation(self.x,self.market,self.mask)
        self.model(self.x,self.market,self.mask,self.anchor*999)
        torch.testing.assert_close(before,self.model.representation(self.x,self.market,self.mask))

    def test_absent_stock_nan_padding_does_not_poison_gradients(self):
        self.mask[:,2]=False;self.x[:,2]=float('nan');self.anchor[:,2]=float('nan')
        pred=self.model(self.x,self.market,self.mask,self.anchor)
        labels=torch.ones(2,3);label_mask=self.mask.any(2);loss=masked_variance_loss(pred,labels,label_mask);loss.backward()
        self.assertTrue(torch.isnan(pred[:,2]).all())
        self.assertTrue(all(torch.isfinite(p.grad).all() for p in self.model.parameters() if p.grad is not None))

    def test_missing_label_keeps_observed_stock_in_attention(self):
        y=torch.ones(2,3);labels=torch.ones(2,3,dtype=torch.bool);labels[:,1]=False
        a=self.model(self.x,self.market,self.mask,self.anchor)
        loss=masked_variance_loss(a,y,labels);self.assertTrue(torch.isfinite(loss))
        changed=self.x.clone();changed[:,1]+=20
        b=self.model(changed,self.market,self.mask,self.anchor)
        self.assertFalse(torch.allclose(a[:,0],b[:,0]))

    def test_stock_permutation_and_positive_output(self):
        order=[2,0,1]
        a=self.model(self.x,self.market,self.mask,self.anchor)
        b=self.model(self.x[:,order],self.market,self.mask[:,order],self.anchor[:,order])
        torch.testing.assert_close(a[:,order],b,atol=1e-5,rtol=1e-5)
        self.assertTrue((e.positive_variance(a)>0).all())

    def test_synthetic_one_epoch_with_masked_labels(self):
        t=batch('2021-01-04','2021-02-05','train');d=batch('2023-01-03','2023-02-03','dev')
        t.target[0,0]=100.;a,b,state=prepare_master(t,d)
        extra=(np.zeros((len(t.dates)*3,2)),np.zeros((len(d.dates)*3,2)))
        r=e.fit_anchor_master(t,d,a,b,extra,dict(width=16,learning_rate=.0001,dropout=.1,max_epochs=1,patience=1),17)
        self.assertEqual(r['epochs'],1);self.assertEqual(len(r['predicted_variance']),int(d.label_mask.sum()))
        self.assertTrue(np.isfinite(r['predicted_variance']).all());self.assertEqual(t.target[0,0],100.)


if __name__=='__main__':unittest.main()
