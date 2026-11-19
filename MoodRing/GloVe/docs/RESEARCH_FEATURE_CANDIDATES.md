# Unfitted volatility structure and cohort helpers

These helpers prepare research inputs. They do not change approved experiment
packets, fit models, publish forecasts or establish a reduction in validation loss.

## Daily variance structure

`research_data.volatility_structure.build_features` accepts a single train or
development partition, an explicit ordered session grid and a supplied universe.
It returns every key, keeping missing sessions rather than compressing history.

For daily vendor-adjusted log return r:

| Field | Calculation | Interpretation |
| --- | --- | --- |
| proxy_variance_1 | 252 × r² | One-session squared-return proxy |
| proxy_variance_5 | 252 × mean of five r² | Weekly trailing proxy |
| proxy_variance_22 | 252 × mean of 22 r² | Monthly trailing proxy |
| negative_variance_22 | 252 × mean(min(r,0)²) | Negative-return contribution |
| positive_variance_22 | 252 × mean(max(r,0)²) | Positive-return contribution |
| peer_variance_22 | Equal mean of other eligible stocks' 22-session proxies | Same-date cohort commonality |
| peer_count / peer_coverage | Number of usable peers / fraction of supplied peers | Support, not sentiment |

All variance fields use annualized squared-return units. Signed components sum
to the total trailing proxy. Zero is an observed value; missing stays null with
a mask. Peer values exclude the subject stock and later cutoffs, require five
peers by default, and never pool across dates. The supplied cohort is not a
broad-market index. Twenty-two observations are required without missing slots;
this differs from the eight-session model context. No preprocessing is fitted.

These are adaptations motivated by HAR persistence and signed-variation research,
not intraday realized-variance estimators. Daily data cannot reconstruct intraday
jump decompositions. The existing five-session future target remains separate.
See the source-linked [research lessons](../src/explanation/research_lessons.json).

## Cohort helper

`research_data.universe_cohort.select_cohort` ranks supplied issuer candidates
solely by USD market cap at a stated selection date, with IPO before 2010. It
reuses the existing identity/exact-value primitives and does not change the
synthetic SIC/valuation/liquidity selector or production membership contracts.

Require one explicitly selected listing per issuer, active listing at selection,
common-stock/US-domicile flags and dated cap inputs. Future IPOs, future cap values,
stale cap inputs and duplicate issuer/share-class entries cannot qualify. Later
exits are retained: requiring survival through the sample would introduce bias.
Same-day cap availability is an end-of-day assumption, not intraday evidence.

The helper ranks the supplied set only. It always reports historical candidate
coverage as unverified and never declares a true historical top-100. Acquiring
complete historical candidates and handling merger/exit returns remain separate
work. IPO age alone cannot remove survivorship or future-size selection bias.

## Validation boundary

Synthetic tests mutate future inputs, introduce missing sessions/later cutoffs,
check signed-variance decomposition and retain future delistings. Real candidate
builds must bind inputs, code and ordered row IDs and report warm-up missingness.
Use training-fitted preprocessing and the same evaluation rows for feature arms.
Do not select a universe because it gives the lowest development loss. Keep an
untouched final test and distinguish exploratory development results from it.
