# Indicator dictionary

This reference documents the implemented conventions, not predictive validity. The machine-readable source is [indicator_dictionary.json](../src/explanation/indicator_dictionary.json). research_data.prepare.technical_frame computes trailing features; the retrospective bridge reads their frozen values without recomputing or fitting.

Frozen vendor-adjusted OHLC; volume retained on provider split basis. Synthetic demonstrations use their declared fictional basis.

## Terms

- **C_t**: Closing price at session t, on the declared adjustment basis.
- **H_t**: High price in session t on the same basis as C_t.
- **L_t**: Low price in session t on the same basis as C_t.
- **V_t**: Provider share volume; never rescaled for dividends.
- **r_t**: Daily log return ln(C_t/C_(t-1)); a fraction, not a percentage.
- **n**: Number of trading sessions or changes in the specified window.
- **mean**: Arithmetic average of the observations in a window.
- **EMA**: Recursive exponential moving average with alpha=2/(span+1), seeded at the first close; warm-up controls visibility.
- **Wilder**: First n changes averaged, then updated as ((n-1)*previous+new)/n.
- **ddof**: Degrees of freedom correction: sample variance divides by n-1; population variance by n.
- **quantile**: Value below which a specified fraction of trailing observations lies; pandas linear interpolation is used.
- **252**: Fixed trading-session annualization convention, not a promise about future volatility.
- **variance**: Squared-return variability; its square root is volatility.
- **QLIKE**: Variance forecast loss log(v)+y/v, for positive predicted annualized variance v and observed annualized variance y.
- **context**: Eight trading-session feature rows used by the forecasting experiment; it is not an indicator warm-up window.
- **cutoff**: Selected historical close-plus-20-minute reference; retrospective values do not prove they were available then.

## Daily price change — return_1

How much the closing price changed since the previous session.

- Calculation: C_t / C_(t-1) - 1; stored as a fraction, multiplied by 100 for percentage display.
- Stored unit: fraction. Percentage displays multiply fractions by 100; annualized variance remains squared-return units.
- Minimum observations: 2. Recursive indicators depend on retained seed history beyond this minimum.
- Implementation: src/research_data/prepare.py:technical_frame; src/explanation/indicators.py:calculate.
- Status: calculated. Descriptive, not a causal explanation or a probability of a rise. Missing inputs remain missing.

## Daily trading range — range_pct

The distance between the day’s high and low, relative to its closing price.

- Calculation: (H_t - L_t) / C_t; stored as a fraction, multiplied by 100 for percentage display.
- Stored unit: fraction. Percentage displays multiply fractions by 100; annualized variance remains squared-return units.
- Minimum observations: 1. Recursive indicators depend on retained seed history beyond this minimum.
- Implementation: src/research_data/prepare.py:technical_frame; src/explanation/indicators.py:calculate.
- Status: calculated. Descriptive, not a causal explanation or a probability of a rise. Missing inputs remain missing.

## Trading volume vs usual — relative_volume_20

1× is the recent average. A larger value means more shares traded; it does not identify buyers or sellers.

- Calculation: Latest volume ÷ average volume of the preceding 20 sessions, excluding today.
- Stored unit: multiple. Percentage displays multiply fractions by 100; annualized variance remains squared-return units.
- Minimum observations: 21. Recursive indicators depend on retained seed history beyond this minimum.
- Implementation: src/research_data/prepare.py:technical_frame; src/explanation/indicators.py:calculate.
- Status: calculated. Descriptive, not a causal explanation or a probability of a rise. Missing inputs remain missing.

## Volume trend · OBV change — obv_change_20

Volume signed by the direction of daily closing-price moves. This is not measured buying or selling pressure.

- Calculation: Over 20 changes: add volume on up days, subtract it on down days, and add zero on unchanged days.
- Stored unit: shares. Percentage displays multiply fractions by 100; annualized variance remains squared-return units.
- Minimum observations: 21. Recursive indicators depend on retained seed history beyond this minimum.
- Implementation: src/research_data/prepare.py:technical_frame; src/explanation/indicators.py:calculate.
- Status: calculated. Descriptive, not a causal explanation or a probability of a rise. Missing inputs remain missing.

## Volume by price level — volume_profile

Shows where trading took place across price levels. Daily totals cannot provide this detail.

- Calculation: Requires intraday trades or suitable volume-at-price data; not calculated from these daily bars.
- Stored unit: not_applicable. Percentage displays multiply fractions by 100; annualized variance remains squared-return units.
- Minimum observations: No connected calculation. Recursive indicators depend on retained seed history beyond this minimum.
- Implementation: No fitted output connected to the card bot.
- Status: not_available. Descriptive, not a causal explanation or a probability of a rise. Missing inputs remain missing.

## Price momentum · RSI — rsi_14

A 0–100 measure of recent gains relative to losses. A high reading alone does not predict a reversal.

- Calculation: 100 − 100 ÷ (1 + average gain ÷ average loss), with a 14-change arithmetic seed and Wilder smoothing. A flat series is undefined.
- Stored unit: index. Percentage displays multiply fractions by 100; annualized variance remains squared-return units.
- Minimum observations: 15. Recursive indicators depend on retained seed history beyond this minimum.
- Implementation: src/research_data/prepare.py:technical_frame; src/explanation/indicators.py:calculate.
- Status: calculated. Descriptive, not a causal explanation or a probability of a rise. Missing inputs remain missing.

## Trend balance · MACD — macd

Positive values mean the faster price average is above the slower one. This describes momentum, not a probability.

- Calculation: 12-session exponential moving average minus the 26-session average, using recursive averages seeded by the first close.
- Stored unit: price. Percentage displays multiply fractions by 100; annualized variance remains squared-return units.
- Minimum observations: 26. Recursive indicators depend on retained seed history beyond this minimum.
- Implementation: src/research_data/prepare.py:technical_frame; src/explanation/indicators.py:calculate.
- Status: calculated. Descriptive, not a causal explanation or a probability of a rise. Missing inputs remain missing.

## MACD signal line — macd_signal

A smoother reference for MACD; crossing it is not a validated trade recommendation.

- Calculation: 9-session recursive exponential average of MACD, following the tool’s warm-up convention; requires 34 closes.
- Stored unit: price. Percentage displays multiply fractions by 100; annualized variance remains squared-return units.
- Minimum observations: 34. Recursive indicators depend on retained seed history beyond this minimum.
- Implementation: src/research_data/prepare.py:technical_frame; src/explanation/indicators.py:calculate.
- Status: calculated. Descriptive, not a causal explanation or a probability of a rise. Missing inputs remain missing.

## MACD gap — macd_hist

The distance between MACD and its signal line.

- Calculation: MACD minus its 9-session signal line; requires 34 closes.
- Stored unit: price. Percentage displays multiply fractions by 100; annualized variance remains squared-return units.
- Minimum observations: 34. Recursive indicators depend on retained seed history beyond this minimum.
- Implementation: src/research_data/prepare.py:technical_frame; src/explanation/indicators.py:calculate.
- Status: calculated. Descriptive, not a causal explanation or a probability of a rise. Missing inputs remain missing.

## Position within price bands — bollinger_pct_b

0% is the lower band and 100% the upper band. Values can fall outside this range; this is not a probability.

- Calculation: (C_t - lower band) / (upper band - lower band); bands are the 20-close mean plus/minus twice the population standard deviation. Stored as a fraction; multiply by 100 for percentage display.
- Stored unit: fraction. Percentage displays multiply fractions by 100; annualized variance remains squared-return units.
- Minimum observations: 20. Recursive indicators depend on retained seed history beyond this minimum.
- Implementation: src/research_data/prepare.py:technical_frame; src/explanation/indicators.py:calculate.
- Status: calculated. Descriptive, not a causal explanation or a probability of a rise. Missing inputs remain missing.

## Price-band width — bollinger_bandwidth

Wider bands describe more dispersion in recent closing prices.

- Calculation: 4 * population standard deviation of 20 closes / their mean; stored as a fraction, multiplied by 100 for percentage display.
- Stored unit: fraction. Percentage displays multiply fractions by 100; annualized variance remains squared-return units.
- Minimum observations: 20. Recursive indicators depend on retained seed history beyond this minimum.
- Implementation: src/research_data/prepare.py:technical_frame; src/explanation/indicators.py:calculate.
- Status: calculated. Descriptive, not a causal explanation or a probability of a rise. Missing inputs remain missing.

## Typical trading range · ATR — atr_14_pct

A smoothed range including overnight gaps, expressed as a share of the latest close. It is not a loss forecast.

- Calculation: True range = max(H_t - L_t, abs(H_t - C_(t-1)), abs(L_t - C_(t-1))). Smooth with a 14-range arithmetic seed and Wilder updates, then divide by C_t. Stored as a fraction; multiply by 100 for percentage display.
- Stored unit: fraction. Percentage displays multiply fractions by 100; annualized variance remains squared-return units.
- Minimum observations: 15. Recursive indicators depend on retained seed history beyond this minimum.
- Implementation: src/research_data/prepare.py:technical_frame; src/explanation/indicators.py:calculate.
- Status: calculated. Descriptive, not a causal explanation or a probability of a rise. Missing inputs remain missing.

## Daily price variability — realized_vol_20

How widely daily log returns varied over the latest 20 changes. This is historical daily volatility, not annualized or predicted volatility.

- Calculation: Sample standard deviation (19 in the denominator) of 20 daily log returns. Stored in fractional return units; multiply by 100 for percentage display. Not annualized.
- Stored unit: daily_log_return_std. Percentage displays multiply fractions by 100; annualized variance remains squared-return units.
- Minimum observations: 21. Recursive indicators depend on retained seed history beyond this minimum.
- Implementation: src/research_data/prepare.py:technical_frame; src/explanation/indicators.py:calculate.
- Status: calculated. Descriptive, not a causal explanation or a probability of a rise. Missing inputs remain missing.

## Volatility forecast · GARCH — garch

A fitted model could estimate changing volatility. No fitted result is supplied here.

- Calculation: Requires a specified model, training window, successful convergence and validated forecasts.
- Stored unit: not_applicable. Percentage displays multiply fractions by 100; annualized variance remains squared-return units.
- Minimum observations: No connected calculation. Recursive indicators depend on retained seed history beyond this minimum.
- Implementation: No fitted output connected to the card bot.
- Status: not_available. Descriptive, not a causal explanation or a probability of a rise. Missing inputs remain missing.

## Extreme-loss estimate · EVT — evt

A tail model could describe unusually large losses. No fitted tail estimate is supplied here.

- Calculation: Requires a training-only threshold, sufficient tail observations and validated tail estimates.
- Stored unit: not_applicable. Percentage displays multiply fractions by 100; annualized variance remains squared-return units.
- Minimum observations: No connected calculation. Recursive indicators depend on retained seed history beyond this minimum.
- Implementation: No fitted output connected to the card bot.
- Status: not_available. Descriptive, not a causal explanation or a probability of a rise. Missing inputs remain missing.

## Portfolio risk — portfolio_risk

The combined risk of several holdings depends on their weights and how returns move together.

- Calculation: Requires portfolio weights and aligned, eligible histories; a single-stock card cannot supply it.
- Stored unit: not_applicable. Percentage displays multiply fractions by 100; annualized variance remains squared-return units.
- Minimum observations: No connected calculation. Recursive indicators depend on retained seed history beyond this minimum.
- Implementation: No fitted output connected to the card bot.
- Status: not_available. Descriptive, not a causal explanation or a probability of a rise. Missing inputs remain missing.

## Chance of a price rise — direction

Proposed probability of a positive return over an accepted horizon.

- Calculation: Requires accepted labels and a validated, calibrated model run. No probability is calculated here.
- Stored unit: not_applicable. Percentage displays multiply fractions by 100; annualized variance remains squared-return units.
- Minimum observations: No connected calculation. Recursive indicators depend on retained seed history beyond this minimum.
- Implementation: No fitted output connected to the card bot.
- Status: not_available. Descriptive, not a causal explanation or a probability of a rise. Missing inputs remain missing.

## Expected price change — expected_return

Proposed average future return over an accepted horizon.

- Calculation: Requires an accepted return basis and horizon, plus a validated model run.
- Stored unit: not_applicable. Percentage displays multiply fractions by 100; annualized variance remains squared-return units.
- Minimum observations: No connected calculation. Recursive indicators depend on retained seed history beyond this minimum.
- Implementation: No fitted output connected to the card bot.
- Status: not_available. Descriptive, not a causal explanation or a probability of a rise. Missing inputs remain missing.

## Forecast uncertainty — variance

The approved research target is annualized five-session realized variance; this bot does not produce a model forecast.

- Calculation: Target y_t = (252/5) * sum(r_(t+j)^2 for j=1..5); report sqrt(v) for a positive predicted variance v. Future labels are never bot inputs.
- Stored unit: not_applicable. Percentage displays multiply fractions by 100; annualized variance remains squared-return units.
- Minimum observations: No connected calculation. Recursive indicators depend on retained seed history beyond this minimum.
- Implementation: No fitted output connected to the card bot.
- Status: not_available. Descriptive, not a causal explanation or a probability of a rise. Missing inputs remain missing.

## Chance of trade success — trade_success

Proposed probability under a defined entry, target, stop and cost policy.

- Calculation: Requires accepted trade labels and a validated model. Daily bars cannot tell which level was hit first when both target and stop are touched.
- Stored unit: not_applicable. Percentage displays multiply fractions by 100; annualized variance remains squared-return units.
- Minimum observations: No connected calculation. Recursive indicators depend on retained seed history beyond this minimum.
- Implementation: No fitted output connected to the card bot.
- Status: not_available. Descriptive, not a causal explanation or a probability of a rise. Missing inputs remain missing.

## Potential adverse price move — drawdown

The proposed head measures a future adverse move from entry. Its definition still needs human acceptance.

- Calculation: Requires an accepted entry, horizon and adverse-move definition, plus a validated model run.
- Stored unit: not_applicable. Percentage displays multiply fractions by 100; annualized variance remains squared-return units.
- Minimum observations: No connected calculation. Recursive indicators depend on retained seed history beyond this minimum.
- Implementation: No fitted output connected to the card bot.
- Status: not_available. Descriptive, not a causal explanation or a probability of a rise. Missing inputs remain missing.

## Daily price difference — price_change_1

Change in the adjusted closing level.

- Calculation: C_t - C_(t-1)
- Stored unit: price. Percentage displays multiply fractions by 100; annualized variance remains squared-return units.
- Minimum observations: 2. Recursive indicators depend on retained seed history beyond this minimum.
- Implementation: src/research_data/prepare.py:technical_frame.
- Status: calculated. Trailing descriptive measure. No calibrated probability or causal claim; retrospective adjustment limitations apply.

## Log adjusted close — log_close

Natural logarithm of the adjusted price level.

- Calculation: ln(C_t); requires C_t > 0
- Stored unit: log_price. Percentage displays multiply fractions by 100; annualized variance remains squared-return units.
- Minimum observations: 1. Recursive indicators depend on retained seed history beyond this minimum.
- Implementation: src/research_data/prepare.py:technical_frame.
- Status: calculated. Trailing descriptive measure. No calibrated probability or causal claim; retrospective adjustment limitations apply.

## Daily log return — log_return_1

Additive return over time on the frozen vendor-adjusted basis.

- Calculation: ln(1 + return_1) = ln(C_t / C_(t-1))
- Stored unit: fraction. Percentage displays multiply fractions by 100; annualized variance remains squared-return units.
- Minimum observations: 2. Recursive indicators depend on retained seed history beyond this minimum.
- Implementation: src/research_data/prepare.py:technical_frame.
- Status: calculated. Trailing descriptive measure. No calibrated probability or causal claim; retrospective adjustment limitations apply.

## Recent variability · 5 sessions — realized_vol_5

Sample daily variability over five log returns.

- Calculation: sqrt(sum((r_i - mean(r))^2)/(5-1)); not annualized
- Stored unit: daily_log_return_std. Percentage displays multiply fractions by 100; annualized variance remains squared-return units.
- Minimum observations: 6. Recursive indicators depend on retained seed history beyond this minimum.
- Implementation: src/research_data/prepare.py:technical_frame.
- Status: calculated. Trailing descriptive measure. No calibrated probability or causal claim; retrospective adjustment limitations apply.

## Recent variability · 63 sessions — realized_vol_63

Sample daily variability over 63 log returns.

- Calculation: sqrt(sum((r_i - mean(r))^2)/(63-1)); not annualized
- Stored unit: daily_log_return_std. Percentage displays multiply fractions by 100; annualized variance remains squared-return units.
- Minimum observations: 64. Recursive indicators depend on retained seed history beyond this minimum.
- Implementation: src/research_data/prepare.py:technical_frame.
- Status: calculated. Trailing descriptive measure. No calibrated probability or causal claim; retrospective adjustment limitations apply.

## Downside variability — downside_semideviation_20

Root mean squared negative log return, using zero as the reference.

- Calculation: sqrt(mean(min(r_i, 0)^2)) over 20 returns; positive returns contribute zero
- Stored unit: fraction. Percentage displays multiply fractions by 100; annualized variance remains squared-return units.
- Minimum observations: 21. Recursive indicators depend on retained seed history beyond this minimum.
- Implementation: src/research_data/prepare.py:technical_frame.
- Status: calculated. Trailing descriptive measure. No calibrated probability or causal claim; retrospective adjustment limitations apply.

## Worst recent daily log return — worst_log_return_63

Smallest retained daily log return in 63 sessions.

- Calculation: min(r_i) over 63 returns; genuine extremes are retained
- Stored unit: fraction. Percentage displays multiply fractions by 100; annualized variance remains squared-return units.
- Minimum observations: 64. Recursive indicators depend on retained seed history beyond this minimum.
- Implementation: src/research_data/prepare.py:technical_frame.
- Status: calculated. Trailing descriptive measure. No calibrated probability or causal claim; retrospective adjustment limitations apply.

## Distance below recent closing peak — drawdown_from_63_close_peak

Current closing price relative to the highest close in 63 sessions, not portfolio drawdown.

- Calculation: C_t / max(C_(t-62), ..., C_t) - 1
- Stored unit: fraction. Percentage displays multiply fractions by 100; annualized variance remains squared-return units.
- Minimum observations: 63. Recursive indicators depend on retained seed history beyond this minimum.
- Implementation: src/research_data/prepare.py:technical_frame.
- Status: calculated. Trailing descriptive measure. No calibrated probability or causal claim; retrospective adjustment limitations apply.

## Historical lower 5% return quantile — historical_log_return_q05_252

A descriptive lower-tail threshold from 252 returns, not a calibrated future VaR.

- Calculation: linear-interpolated quantile(r, 0.05) over 252 returns
- Stored unit: fraction. Percentage displays multiply fractions by 100; annualized variance remains squared-return units.
- Minimum observations: 253. Recursive indicators depend on retained seed history beyond this minimum.
- Implementation: src/research_data/prepare.py:technical_frame.
- Status: calculated. Trailing descriptive measure. No calibrated probability or causal claim; retrospective adjustment limitations apply.

## Historical lower-tail mean — historical_log_return_tail_mean_252

Mean of returns at or below the trailing 5% threshold; ties are included.

- Calculation: mean(r_i where r_i <= quantile(r, 0.05)) over 252 returns
- Stored unit: fraction. Percentage displays multiply fractions by 100; annualized variance remains squared-return units.
- Minimum observations: 253. Recursive indicators depend on retained seed history beyond this minimum.
- Implementation: src/research_data/prepare.py:technical_frame.
- Status: calculated. Trailing descriptive measure. No calibrated probability or causal claim; retrospective adjustment limitations apply.

## Historical variance benchmark — historical_variance_20

Annualized mean squared daily log returns; differs from sample standard deviation squared.

- Calculation: 252 * mean(r_i^2) over 20 returns; no mean subtraction
- Stored unit: annualized_variance. Percentage displays multiply fractions by 100; annualized variance remains squared-return units.
- Minimum observations: 21. Recursive indicators depend on retained seed history beyond this minimum.
- Implementation: src/research_data/retrospective_panel.py:price_features.
- Status: calculated. Trailing descriptive measure. No calibrated probability or causal claim; retrospective adjustment limitations apply.

## Missingness and educational interpretation

Do not initialize undefined RSI to 50, missing returns to zero, or absent sentiment to neutral. ATR is a range measure, not a guaranteed loss bound; Bollinger bands are not calibrated probability intervals. The historical variance benchmark uses mean squared returns, whereas realized_vol uses centered sample standard deviation. The five-session future target is documented for understanding and is never an explanation-bot input.
