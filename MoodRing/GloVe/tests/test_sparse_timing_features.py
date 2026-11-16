import unittest
import numpy as np
import pandas as pd
from research_data.sparse_timing_features import daily_groups, context_features


class MechanicsTest(unittest.TestCase):
    def setUp(self):
        self.days = ['2024-12-18','2024-12-19','2024-12-20','2024-12-23','2024-12-24','2024-12-26','2024-12-27','2024-12-30','2024-12-31','2025-01-02']
        self.cal = pd.DataFrame({'session': self.days})
        self.rows = pd.DataFrame([{'row_id':'r','ticker':'AAA','session':'2024-12-31','context_sessions':self.days[1:9]}])
        self.support = pd.DataFrame([{'row_id':'r','lag':lag,'supported':True} for lag in (0,1,5)])
        self.scores = pd.DataFrame([dict(exact_model_input_sha256=h,positive=p,negative=n,neutral=1-p-n,tone=p-n,status='scored') for h,p,n in [('x',.8,.1),('y',.1,.8),('z',.2,.2)]])
    def obs(self, specs):
        return pd.DataFrame([dict(observation_id=str(i),ticker='AAA',session=day,split='dev',sentiment_channel='direct',exact_model_input_sha256=h,state=state,policy_observation_eligible=True,exclusion=None,availability_at=day+'T18:00:00Z',cutoff=day+'T18:20:00Z') for i,(day,h,state) in enumerate(specs)])
    def test_duplicates_equal_day_and_repeat_identity(self):
        o = self.obs([(self.days[1],'x','scored')]*3+[(self.days[1],'y','scored'),(self.days[2],'x','scored')])
        _,g,d=daily_groups(o,self.scores,self.cal,0)
        c=context_features(self.rows,d,g,self.support,0).query("channel=='direct'").iloc[0]
        self.assertAlmostEqual(c.sentiment,.35)
        self.assertEqual(c.context_distinct_scored_inputs,2)
        self.assertAlmostEqual(c.largest_input_weight,.75)
        self.assertAlmostEqual(c.effective_input_count,1/(.75**2+.25**2))
        self.assertAlmostEqual(c.support_weighted_tone,(0+.7/6)/2)
    def test_neutral_is_observed_and_absent_is_missing(self):
        _,g,d=daily_groups(self.obs([(self.days[1],'z','scored')]),self.scores,self.cal,0)
        c=context_features(self.rows,d,g,self.support,0).set_index('channel')
        self.assertEqual(c.loc['direct','sentiment'],0)
        self.assertEqual(c.loc['direct','state'],'scored')
        self.assertTrue(np.isnan(c.loc['indirect_product','sentiment']))
        self.assertEqual(c.loc['indirect_product','state'],'absent_news')
    def test_overlength_stays_null(self):
        _,g,d=daily_groups(self.obs([(self.days[1],'long','overlength')]),self.scores,self.cal,0)
        c=context_features(self.rows,d,g,self.support,0).query("channel=='direct'").iloc[0]
        self.assertEqual(c.state,'overlength_only');self.assertTrue(np.isnan(c.sentiment))
    def test_exchange_delay_holiday_and_partition(self):
        original=self.obs([(self.days[4],'x','scored'),(self.days[8],'y','scored')])
        shifted,g,d=daily_groups(original,self.scores,self.cal,1)
        self.assertEqual(shifted.effective_session.tolist(),['2024-12-26','2025-01-02'])
        self.assertEqual(shifted.delay_status.tolist(),['eligible_dev','outside_dev'])
        self.assertEqual(shifted.availability_at.tolist(),original.availability_at.tolist())
        self.assertEqual(len(g),1)
    def test_unsupported_not_absent(self):
        _,g,d=daily_groups(self.obs([(self.days[1],'x','scored')]),self.scores,self.cal,0)
        self.support.loc[self.support.lag==0,'supported']=False
        c=context_features(self.rows,d,g,self.support,0)
        self.assertEqual(set(c.state),{'unsupported_delay_window'})
        self.assertTrue(c.sentiment.isna().all())
    def test_receipt_conflict_refused(self):
        with self.assertRaises(ValueError):
            daily_groups(self.obs([(self.days[1],'x','scored')]),pd.concat([self.scores,self.scores]),self.cal,0)
    def test_future_observation_refused(self):
        o=self.obs([(self.days[1],'x','scored')]);o.loc[0,'availability_at']='2024-12-20T19:00:00Z'
        with self.assertRaises(ValueError):daily_groups(o,self.scores,self.cal,0)
    def test_sector_polarity_not_reversed(self):
        o=self.obs([(self.days[1],'y','scored')]);o['sentiment_channel']='indirect_sector_competitor'
        _,g,d=daily_groups(o,self.scores,self.cal,0)
        c=context_features(self.rows,d,g,self.support,0).query("channel=='indirect_sector_competitor'").iloc[0]
        self.assertAlmostEqual(c.sentiment,-.7)
        self.assertAlmostEqual(c.support_weighted_tone,-.7/6)


if __name__=='__main__':unittest.main(verbosity=2)


def test_review_quarantines_exact_company_mapping_without_score_changes():
    from research_data.sparse_timing_features import review_overlay
    from copy import deepcopy
    cases=[dict(review_id='a',ticker='AAA',source_record_sha256='s',exact_model_input_sha256='h',session='2024-01-03'),
           dict(review_id='b',ticker='BBB',source_record_sha256='s',exact_model_input_sha256='h',session='2024-01-03')]
    obs=pd.DataFrame([dict(c,split='dev',tone=-.8,availability_at='2024-01-03T19:00:00Z') for c in cases])
    review=dict(schema='moodring-development-headline-review-v1',source_packet_sha256='bound',
        reviewer='synthetic',completed_at='2026-11-16T00:00:00Z',provenance={'human_completed':True},
        cases=[dict(c,labels={'decision':d,'generic_tone':'positive'}) for c,d in zip(cases,['quarantine pending evidence','retain current mapping'])])
    original=obs.copy(deep=True)
    keep,held,audit=review_overlay(obs,review,cases,'bound')
    assert keep.ticker.tolist()==['BBB'] and held.ticker.tolist()==['AAA'] and keep.tone.iloc[0]==-.8
    pd.testing.assert_frame_equal(obs,original)
    assert not audit['human_accuracy_established']
    import pytest
    with pytest.raises(ValueError):review_overlay(obs,review,cases,'changed')
    wrong=deepcopy(review);wrong['cases'][0]['ticker']='BBB'
    with pytest.raises(ValueError):review_overlay(obs,wrong,cases,'bound')
    obs.loc[0,'split']='train'
    with pytest.raises(ValueError):review_overlay(obs,review,cases,'bound')
