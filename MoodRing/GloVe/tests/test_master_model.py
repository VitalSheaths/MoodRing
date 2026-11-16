"""Architecture invariants using synthetic tensors; no financial performance claim."""
import unittest
try:
    import torch
except ImportError:
    raise unittest.SkipTest("optional local torch research environment required")
from research_data.master_model import MarketGuidedStockTransformer


class MasterArchitectureTests(unittest.TestCase):
    def setUp(self):
        torch.manual_seed(17);torch.set_num_threads(2)
        self.model=MarketGuidedStockTransformer(6,3,dropout=0).eval()
        self.x=torch.randn(2,10,8,6);self.market=torch.randn(2,3)

    def test_joint_scores_and_trainable_gradients(self):
        output=self.model(self.x,self.market)
        self.assertEqual(tuple(output.shape),(2,10))
        output.square().mean().backward()
        self.assertTrue(all(p.grad is not None and torch.isfinite(p.grad).all() for p in self.model.parameters()))

    def test_stock_permutation_equivariance(self):
        order=torch.randperm(10)
        torch.testing.assert_close(self.model(self.x[:,order],self.market),self.model(self.x,self.market)[:,order],atol=1e-5,rtol=1e-5)

    def test_prediction_dates_never_attend_to_each_other(self):
        other=self.x.clone();other[1]*=100
        torch.testing.assert_close(self.model(self.x,self.market)[0],self.model(other,self.market)[0])

    def test_reject_missing_or_future_unbounded_window(self):
        bad=self.x.clone();bad[0,0,0,0]=float('nan')
        with self.assertRaises(ValueError):self.model(bad,self.market)
        with self.assertRaises(ValueError):self.model(torch.randn(2,10,65,6),self.market)

    def test_inter_stock_ablation_disables_other_stock_influence(self):
        model=MarketGuidedStockTransformer(6,3,dropout=0,inter_stock=False).eval()
        other=self.x.clone();other[:,1:]*=100
        torch.testing.assert_close(model(self.x,self.market)[:,0],model(other,self.market)[:,0])


class MaskAndRiskBoundaryTests(unittest.TestCase):
    def setUp(self):
        torch.manual_seed(9);torch.set_num_threads(2)
        self.m=MarketGuidedStockTransformer(6,3,dropout=0).eval()
        self.x=torch.randn(2,4,8,6);self.market=torch.randn(2,3)
        self.mask=torch.ones(2,4,8,dtype=torch.bool)

    def test_absent_stock_and_tokens_cannot_change_other_predictions(self):
        self.mask[:,3,:]=False;self.mask[:,1,2:4]=False
        a=self.m(self.x,self.market,self.mask)
        changed=self.x.clone();changed[~self.mask]=float('nan')
        b=self.m(changed,self.market,self.mask)
        torch.testing.assert_close(a[:,:3],b[:,:3]);self.assertTrue(torch.isnan(b[:,3]).all())
        # Completely absent time slice must not cause all-masked attention NaNs.
        self.mask[:,:,0]=False
        self.assertTrue(torch.isfinite(self.m(changed,self.market,self.mask)[:,:3]).all())

    def test_missing_labels_do_not_remove_stock_context_and_extremes_remain(self):
        from research_data.master_model import masked_variance_loss
        scores=self.m(self.x,self.market,self.mask);labels=torch.ones(2,4)
        mask=torch.ones_like(labels,dtype=torch.bool);mask[:,1]=False;labels[:,1]=float('nan')
        loss=masked_variance_loss(scores,labels,mask);loss.backward()
        self.assertTrue(torch.isfinite(loss))
        extreme=labels.clone();extreme[0,0]=1000
        self.assertGreater(masked_variance_loss(scores,extreme,mask),loss)
        self.assertIsNone(masked_variance_loss(scores,labels,torch.zeros_like(mask)))
        altered=self.x.clone();altered[:,1]+=10
        self.assertFalse(torch.allclose(self.m(altered,self.market)[:,0],scores[:,0]))

    def test_market_and_stock_availability_share_cutoff(self):
        from research_data.master_model import verify_batch_times
        times=torch.full((2,4,8),99,dtype=torch.int64);market=torch.full((2,3),99,dtype=torch.int64)
        cutoff=torch.full((2,),100,dtype=torch.int64)
        verify_batch_times(times,market,cutoff,self.mask)
        market[1,0]=101
        with self.assertRaises(ValueError):verify_batch_times(times,market,cutoff,self.mask)
        market[1,0]=99;times[0,0,0]=101
        with self.assertRaises(ValueError):verify_batch_times(times,market,cutoff,self.mask)

    def test_asof_entrypoint_checks_every_feature_time(self):
        times=torch.full((2,4,8),99,dtype=torch.int64)
        market_times=torch.full((2,3),99,dtype=torch.int64)
        cutoff=torch.full((2,),100,dtype=torch.int64)
        torch.testing.assert_close(self.m.forward_asof(self.x,self.market,times,market_times,cutoff,self.mask),self.m(self.x,self.market,self.mask))
        with self.assertRaises(ValueError):
            self.m.forward_asof(self.x,self.market,times,market_times[:,:1],cutoff,self.mask)
        market_times[0,2]=101
        with self.assertRaises(ValueError):
            self.m.forward_asof(self.x,self.market,times,market_times,cutoff,self.mask)

    def test_variance_prediction_uses_loss_link_and_masks_absent_stocks(self):
        from research_data.master_model import positive_variance,masked_variance_loss
        times=torch.full((2,4,8),99,dtype=torch.int64);mt=torch.full((2,3),99,dtype=torch.int64)
        cutoff=torch.full((2,),100,dtype=torch.int64);self.mask[:,3]=False
        raw=self.m.forward_asof(self.x,self.market,times,mt,cutoff,self.mask)
        variance=self.m.predict_variance_asof(self.x,self.market,times,mt,cutoff,self.mask)
        torch.testing.assert_close(variance,positive_variance(raw),equal_nan=True)
        self.assertTrue((variance[:,:3]>0).all());self.assertTrue(torch.isnan(variance[:,3]).all())
        mask=self.mask.any(2);target=torch.ones_like(raw)*.04
        torch.testing.assert_close(masked_variance_loss(raw,target,mask),
            (torch.log(variance[mask])+target[mask]/variance[mask]).mean())
        self.assertGreater(positive_variance(torch.tensor(-1000.)).item(),0)


if __name__=='__main__':unittest.main()
