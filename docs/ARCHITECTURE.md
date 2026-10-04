# Current architecture, API and synchronization

This document describes existing behavior. The proposed public architecture is separately labeled in DEPLOYMENT.md. Editable diagrams are in diagrams/; rendered Mermaid sources are embedded in the root README.

## Modules and responsibilities

| Module | Responsibility |
|---|---|
| run.py | Adds src to the Python path, sets the default project var directory, launches the app |
| app.py | HTTP routing, active dataset storage, job creation/status, saved projects, file serving, batch execution, commentary revisions and SVG export |
| bi_engine.py | CSV/Excel reading, profile/reshaping, filtered regular series preparation, core models, metrics, chart and document exports |
| bi_plus.py | Mandatory quality acknowledgement, added models, driver handling, candidate selection, final test, calibration, scenarios and explanatory evidence |
| r_exports.py | Escaped embedded evidence in Rmd/R code, R checks and base-R chart recipes |
| bi.html | Visual theme, input form, static fallback results and API helper |
| workspace.js | Review and acknowledgement, project/report library, comparison results and note revisions |
| charts.js | SVG visualizations, visible-series/range state, interaction and expanded chart dialogs |

The static scripts load in order: inline dashboard helpers, workspace.js, then charts.js. The charts layer wraps result rendering and loads saved chart evidence. There is no Node runtime, bundler, CDN chart dependency, OpenAI API, autonomous web search or execution of uploaded code.

## HTTP interface

JSON POSTs use Content-Type application/json. Browser requests are local same-origin. Generated identifiers are lowercase 32-character UUID hex strings. Path files are constrained to the completed job's recorded files; static chart exports allow a restricted SVG shape/attribute set.

| Method and route | Input / purpose | Result |
|---|---|---|
| GET / | Browser dashboard | HTML |
| GET /workspace.js, /charts.js | Local assets | JavaScript |
| GET /healthz | Liveness | JSON status; does not test forecast quality |
| GET /api/config | Supported model choices | Local mode and model list |
| GET /sample.csv | Synthetic demonstration input | CSV |
| POST /api/datasets | filename, base64 data, orientation, optional sheet | Active dataset id; columns, categories and preview |
| POST /api/review | dataset_id, config | Ready/block status, issues and quality metadata |
| POST /api/jobs | dataset_id, config | Queued job id; optional batch_column selects comparison execution |
| GET /api/jobs/id | Job identifier | Running stage, failed reason, or completed report/forecast/files |
| GET /api/jobs/id/charts | Completed analysis identifier | Prepared history and backtest rows |
| GET /api/library | Current-format saved reports/comparisons | Most recent completed analyses |
| GET /api/projects | Saved source/config snapshots | Project metadata |
| POST /api/projects/save | dataset_id, name, config | New immutable snapshot id and saved metadata |
| POST /api/projects/load | id | Verified source snapshot, profile and restored settings |
| POST /api/notes | job_id, notes | New report id linked to prior revision |
| POST /api/chart-export | Restricted static SVG string | Local chart download URL |
| GET /files/id/name | Listed completed report artifact | Download; SVG forecast is displayable inline |
| GET /chart-downloads/id.svg | Exported visible chart | Downloadable SVG |

The interface is an internal local API, not a versioned public contract. There is no authentication, tenant identifier, database transaction, cancellation API, webhook, automatic data refresh or server-executed R endpoint.

## Upload and preparation

1. Browser reads the selected file, enforces a size cap and encodes bytes as base64 inside JSON.
2. Server rechecks request limits, parses the file and hashes original bytes.
3. UTF-8 CSV is read as a table; Excel readers inspect available sheets. Expanded XLSX size is capped before parsing.
4. Wide layout identifies period headings and reshapes them into time/value records while retaining identifiers.
5. Profile returns data preview, candidate date/numeric columns and categorical filter choices. The analyst chooses the actual fields.
6. Review filters the uploaded frame and flags quality issues. Series preparation parses dates/values, checks segment ambiguity, groups by selected frequency and aggregation, and regularizes periods.
7. Missing source values and malformed dates block processing. Absent time periods either block or use past-only carry-forward. No zero fill or future-aware interpolation is performed.

The quality acknowledgement is a client interaction gate; the backend also recomputes review and requires review_accepted. It is not a signed approval or identity-bound audit trail. A public system needs persisted, user-scoped approval records.

## Concurrency and status updates

The current service uses ThreadingHTTPServer for requests and a daemon thread for one active forecast/batch. A shared lock protects selected job-state mutations. Dataset replacement/project loading is blocked while an analysis is running. Batch groups are evaluated sequentially; the root job records progress and each member gets its own identifier.

The browser polls about every 1.2 seconds while the job runs. Intermediate messages report stages, not a reliable completion percentage. Success returns saved evidence and file names. Failure records a reason. This is local job-status synchronization, not continuous source synchronization.

## Storage and lifecycle

| State | Location | Restart behavior |
|---|---|---|
| Active frame and original uploaded bytes | Process memory | Lost unless saved as a project |
| Running job/thread and status | Process memory | Interrupted; not automatically resumed |
| Completed numerical evidence | var/reports/id | Retained; reconstructed into a job response when opened |
| Saved source snapshot and configuration | var/projects/id | Retained; hash verified when loaded |
| Visible-view chart export | var/charts/id.svg | Retained |
| UI chart toggles, selected point and range | Browser memory | Reset when re-rendered/reloaded |

Report files are currently written into folders without a durable transaction/status database. A crash during export can leave partial folders. Public deployment needs atomic publication, recoverable jobs and incomplete-artifact cleanup. Several web processes would each have independent dataset/job dictionaries and cannot be added safely without replacing that architecture.

Source refresh is manual: edit/download your upstream spreadsheet, upload it again, review and create a new analysis. Opening a snapshot reads stored bytes; it does not fetch an updated URL. Source URL fields are citations and are not crawled. No country/state facts or currencies are automatically reconciled.

## Model evidence

ARIMA/SARIMA candidate orders are chosen only on the earliest training window. Expanding chronological windows assess models; ensemble components also come from these rolling scores. Their displayed errors can be optimistic because they support selection. The excluded final block assesses the selected model and interval adjustment before the production fit includes all selected historical observations.

Widen-only calibration uses pooled rolling absolute errors and a finite-sample quantile; it does not guarantee independent/conformal coverage for dependent time series. Final-test bounds and future bounds may be wider than nominal model bands. Reported rolling coverage is computed on its original exported rolling intervals. This distinction allows R verification to reproduce the metrics.

SARIMAX standardizes its historical driver separately for each training window and needs an explicit future driver path. Test driver outcomes are treated as known, so its scores are conditional. Scenarios compound analyst-selected annual multipliers over elapsed calendar years relative to the baseline.

## Reporting and R

Numerical evidence is exported once, then drives UI charts and reports. Analyst-commentary revisions reuse the same history/forecast/backtests, create a new report id and record revision_of. Rmd/R embed those selected observations and metadata as escaped JSON strings. User strings are data, not executable R or injected Markdown chunks.

R checks are independent recomputations of exported metrics and structural consistency; original raw-source preparation and Python model fitting are not rerun in R. Rmd knitting requires an external local R installation. The dashboard itself does not invoke R, install R packages or accept executable R uploads.
