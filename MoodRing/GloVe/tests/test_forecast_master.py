"""Synthetic MASTER runner checks in optional local Torch environment."""
from copy import deepcopy
import unittest
import numpy as np
try:
    import torch
except ImportError:
    raise unittest.SkipTest('optional local torch research environment required')
from research_data.forecast_master import MasterBatch,prepare_master,fit_master_synthetic_or_released
from research_data.point_in_time import sessions_between


def batch(start,end,split):
    sessions=sessions_between(start,end);selected=list(range(7,min(11,len(sessions)-5)))
    dates=tuple(sessions[i][0] for i in selected);tickers=('SYN1','SYN2','SYN3')
    contexts=tuple(tuple(d for d,_ in sessions[i-7:i+1]) for i in selected)
    x=np.zeros((len(dates),3,8,2));mask=np.ones(x.shape[:3],dtype=bool)
    times=np.zeros(mask.shape,dtype=np.int64);cutoffs=[];market=[];market_times=[]
    for a,i in enumerate(selected):
        cutoff=int(sessions[i][1].timestamp()*1e9)+20*60*10**9;cutoffs.append(cutoff)
        market.append([float(i),np.nan]);market_times.append([cutoff,0])
        for b in range(3):
            for c,j in enumerate(range(i-7,i+1)):
                x[a,b,c]=[j+b,np.nan if j%3==0 else j*.1]
                times[a,b,c]=int(sessions[j][1].timestamp()*1e9)
    mask[:,2]=False;x[:,2]=np.nan;times[:,2]=0
    target=np.full((len(dates),3),.04);label_mask=np.ones(target.shape,dtype=bool)
    label_mask[:,1:]=False;target[:,1:]=np.nan
    return MasterBatch(split,dates,tickers,contexts,('price','macro'),('market','absent_market'),
        x,np.asarray(market),mask,target,label_mask,tuple(sessions[i+5][0] for i in selected),
        times,np.asarray(market_times,dtype=np.int64),np.asarray(cutoffs,dtype=np.int64))


class ForecastMasterTests(unittest.TestCase):
    def setUp(self):
        self.train=batch('2021-01-04','2021-02-05','train')
        self.dev=batch('2023-01-03','2023-02-03','dev')

    def test_unique_context_scaling_masks_and_unlabeled_stock_preserved(self):
        a,b,state=prepare_master(self.train,self.dev)
        self.assertLess(state['unique_training_stock_features'],self.train.token_mask.sum())
        self.assertEqual(state['market_dropped'],['absent_market'])
        self.assertTrue(a['token_mask'][:,1].all())
        self.assertFalse(a['label_mask'][:,1].any())
        self.assertTrue((a['x'][:,2]==0).all())
        changed=deepcopy(self.dev);changed.x[changed.token_mask]*=1000;changed.market[:,0]*=1000
        self.assertEqual(state,prepare_master(self.train,changed)[2])

    def test_future_market_stock_context_target_boundaries(self):
        for kind in ('market','stock','cutoff','context','horizon','test','label'):
            bad=deepcopy(self.dev)
            if kind=='market':bad.market_times[0,0]=bad.cutoffs[0]+1
            if kind=='stock':bad.stock_times[0,0,0]=bad.cutoffs[0]+1
            if kind=='cutoff':bad.cutoffs[0]=bad.cutoffs[0]+24*60*60*10**9
            if kind=='context':bad.contexts=(('2022-12-30',)+bad.contexts[0][1:],)+bad.contexts[1:]
            if kind=='horizon':bad.label_end_sessions=(bad.label_end_sessions[1],)+bad.label_end_sessions[1:]
            if kind=='test':bad.split='test'
            if kind=='label':bad.label_mask[0,2]=True;bad.target[0,2]=.1
            with self.assertRaises(ValueError,msg=kind):prepare_master(self.train,bad)

    def test_duplicate_feature_disagreement_fails(self):
        bad=deepcopy(self.train);bad.x[1,0,0,0]+=1
        with self.assertRaises(ValueError):prepare_master(bad,self.dev)

    def test_small_synthetic_qlike_loop_positive_variance_and_missing_stock(self):
        self.train.target[0,0]=100.
        result=fit_master_synthetic_or_released(self.train,self.dev,max_epochs=2,patience=1)
        self.assertLessEqual(result['epochs'],2)
        self.assertTrue((result['predicted_variance'][:,:2]>0).all())
        self.assertTrue(np.isnan(result['predicted_variance'][:,2]).all())
        self.assertEqual(result['extreme_targets_removed'],0)
        self.assertEqual(self.train.target[0,0],100.)
        self.assertFalse(result['final_test_evaluated'])
        self.assertEqual(result['fitting_objective'],'QLIKE annualized variance')

    def test_dates_remain_isolated_in_returned_model(self):
        a,b,_=prepare_master(self.train,self.dev)
        result=fit_master_synthetic_or_released(self.train,self.dev,max_epochs=1,patience=1)
        model=result['model'].eval();x=torch.tensor(b['x'],dtype=torch.float32);market=torch.tensor(b['market'],dtype=torch.float32)
        mask=torch.tensor(b['token_mask']);changed=x.clone();changed[1]*=100
        with torch.no_grad():
            torch.testing.assert_close(model(x,market,mask)[0],model(changed,market,mask)[0],equal_nan=True)


if __name__=='__main__':unittest.main()
