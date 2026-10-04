# Input contract and analyst decisions

## Supported files and layouts

- UTF-8 CSV, XLSX and XLS; maximum original input size 10 MiB.
- First row is the header. Avoid merged headings, descriptive titles above the table or mixed tables in one sheet.
- Select one worksheet and the correct orientation. Dates down rows supports arbitrary column names chosen by the analyst.
- Years/dates across columns reshapes recognized headings into `_period` and `_value`. Identifier columns stay available for filtering.
- If measures occupy rows, keep a measure-name identifier, reshape as needed, then filter to one measure. The application cannot infer every possible spreadsheet layout.
- Limits: 100,000 source rows, 1,000 source columns, 200,000 reshaped rows, 2,500 regular periods and an expanded XLSX cap of 100 MiB. SARIMA/Compare all has a 1,000-period preparation cap.

## Required series properties

Use dated repeated observations of the same metric and market definition, with consistent units. At least twenty regular observations are required overall. Seasonal evaluation typically requires more than two complete cycles plus eight earliest-training observations, rolling windows and an untouched final block. Exact minimum depends on horizon, season and number of folds; the application reports why a selection is unsupported.

Do not treat forecasts already published by a market vendor as observed ground truth. Set figure type to Already forecasted figures; the review warns that new extrapolation compounds original assumptions. Synthetic/demo inputs must remain labeled synthetic.

Unambiguous dates such as 2024-01-01 are preferable. Day/month/year parsing is optional. Annual headings may be reshaped as period starts. Monthly/quarterly/annual bins use period-start labels; weekly bins are Monday-ending. Choose frequency before interpreting how values were aggregated.

## Units and aggregation

- Sum suits flows across a period, such as daily unit sales aggregated to a month, when the underlying intervals do not overlap.
- Mean or last often suits a snapshot market size or price index. Summing twelve monthly snapshots does not generally give annual market size.
- Rates, CAGR, shares and indices are non-level metrics. Their aggregation uses mean or last; the app blocks summing them.
- A simple mean of percentages is not automatically a weighted rate. Prepare weighted inputs upstream when the denominator matters.
- Mixed values in fields named currency/unit block analysis. These checks do not discover every hidden unit embedded in a title or number.
- No FX conversion, inflation/deflator adjustment, total/component reconciliation or geography harmonization is performed.

## Missing data, duplicates and outliers

Missing/non-numeric/infinite measured values and invalid dates block processing instead of disappearing. Missing periods block by default; carry-forward is an explicit causal choice that reuses the most recent past observation. It does not add genuinely measured information.

Exact duplicate rows are flagged but retained. Repeated time labels are aggregated using the selected rule. Remove unintended duplicates in the source. Outlier screening uses broad IQR fences and retains points: trend/seasonality can look unusual without being an error. Potential overlapping aggregate labels are flagged when combining segments, but absence of a warning does not prove independence.

## Geographic and segment scope

Country/state/region/sector/segment labels are those in the upload. Choose one coherent set of filters. Remaining varying categorical dimensions require explicit permission to combine. A batch field creates independent groups, with other filters still applied. At most twelve groups run per comparison. Do not rank endpoints across different units or overlapping territories.

## Configuration choices

| Setting | Accepted purpose / range |
|---|---|
| model | One supported model or Compare all; selected before running |
| horizon | 1–36 future periods |
| confidence | Nominal 80%, 90% or 95% |
| season | 1–52 periods; seasonal models require at least 2 |
| validation_folds | 3–6 chronological windows, plus separate final test |
| calibrate_intervals | Enabled by default; widens bounds using rolling errors |
| upside_growth/downside_growth | Ordered annual adjustments greater than -99% and up to 100% |
| window_start | Optional explicit history start date; no automatic truncation |
| batch_column | Blank for one series or a selected categorical dimension |
| driver_column/future_driver | SARIMAX only: separate numeric driver, one finite future value per period |
| source_evidence | User-supplied publisher, citation, publication date, figure type and methodology |

A seasonal monthly series often uses season=12, quarterly=4 or weekly=52, but the right period depends on the data-generating pattern. Daily season=7 can represent day-of-week effects. Annual data with no recurring multi-year cycle may need a nonseasonal model instead.

The config.sample.json file is a documented API example, not an auto-import feature. The browser supplies its own configuration. Leave review_accepted=false in templates; the analyst must actually read the review before accepting it. Configuration settings are included in findings.json for later inspection.

## SARIMAX assumptions

Align a separate numeric historical driver to the selected target dates. The driver is aggregated with mean and standardized within each training window. Enter exactly horizon future driver values separated by commas/spaces. Historical tests condition on observed future driver outcomes; future predictions condition on your supplied path. This omits driver uncertainty and does not establish causal effect or real-time forecasting accuracy.

## Interpreting deliverables

- RMSE/MAE use target units. Do not compare them directly across differently scaled targets.
- sMAPE is a symmetric percentage measure with zero-denominator pairs contributing zero.
- MASE uses a training-derived naive-difference scale; below 1 does not automatically imply beating the actual held-out last-value baseline.
- Coverage is the percentage of held-out observations inside exported bounds; a handful of points cannot establish stable future coverage.
- Final-test naive RMSE is a direct held-out last-value baseline for the final block.
- CAGR is calendar-time endpoint growth for positive levels. Projected CAGR comes from the modeled endpoint, not an externally confirmed growth rate.
- Upside/downside paths are user-assumed multipliers. Negative levels/rate/index scenarios should not be interpreted as market growth.

Use the source register and analyst commentary to explain collection limitations and business context. The application does not discover external proofs, verify publishers or guarantee market precision.
