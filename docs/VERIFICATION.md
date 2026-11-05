# Release 1.1 verification

Local Python 3.12: **37 automated tests passed**, including real Kaggle SARIMA/model comparison/SARIMAX/batch forecasts, report exports, mandatory review, upload bounds, source hashes, account expiry/revocation, CSRF/viewer restrictions, backup integrity and interrupted-job records.

A Chromium end-to-end test passed: Kaggle loading, explicit model selection, quality acknowledgement, forecasting, six interactive charts, expanded dialog, PDF download, project/report persistence after reload, commentary revision and mobile overflow checks. No uncaught page errors were observed.

Kaggle input is historical educational retail data; these checks do not certify present-day market accuracy. The default West/Technology series contains 47 observed monthly periods, starting February 2015.

The Vercel Python bundle built successfully using Python 3.12. Final production URL/commit and post-deploy checks are recorded in `docs/RELEASE.md`. The production build uses a request-scoped temporary directory, with browser IndexedDB library persistence.

R Markdown/verification files are generated and structurally checked. R/Pandoc knitting remains unverified on this machine. Docker/Compose startup remains unverified without a running Docker daemon/Compose plugin. Remote CI is verified separately through its run status.

This is a functional hosted research application within documented limits, not a completed enterprise multi-tenant SaaS. See HOSTED.md for explicit boundaries.
