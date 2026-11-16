import hashlib
import pandas as pd
import pytest
from research_data.development_diagnostics import errors,coverage_summary,verify_headline_trace


def test_floor_extreme_and_coverage_stay_visible():
    rows=pd.DataFrame(dict(row_id=['a','b'],session=['2023-01-03','2023-01-04'],ticker=['SYN','SYN'],
        annualized_variance=[100.,.04],direct_scored_days=[0,8],indirect_product_scored_days=[1,4],indirect_sector_competitor_scored_days=[0,0]))
    predictions=dict(row_ids=['a','b'],keys=[['2023-01-03','SYN'],['2023-01-04','SYN']],predicted_variance=[1e-8,.04])
    out=errors(rows,predictions);assert len(out)==2 and out.iloc[0].qlike>1e9
    summary=coverage_summary(out);assert summary.rows.sum()==6 and summary.floor_rows.sum()==3
    with pytest.raises(ValueError):errors(rows.iloc[::-1],predictions)
    rows.loc[0,'session']='2025-01-03'
    with pytest.raises(ValueError):errors(rows,predictions)


def test_title_binding_time_and_review_provenance():
    title='Synthetic competitor reports lower revenue'
    r=dict(title=title,exact_model_input_sha256=hashlib.sha256(title.encode()).hexdigest(),
        forecast_session='2024-01-04',session='2024-01-03',availability_at='2024-01-03T20:00:00Z',forecast_cutoff='2024-01-04T21:20:00Z')
    assert not verify_headline_trace([r])['human_accuracy_established']
    with pytest.raises(ValueError):verify_headline_trace([dict(r,title='Changed')])
    with pytest.raises(ValueError):verify_headline_trace([dict(r,availability_at='2024-01-05T20:00:00Z')])
