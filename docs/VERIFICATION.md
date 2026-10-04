# Verification record

## Automated tests

Run from the project root using Python 3.12 and installed requirements:

```sh
python -m unittest discover -s tests -p 'test*.py'
```

The structured release contains 21 tests. They verify source parsing, wide Excel reshaping, segment ambiguity, invalid/missing data handling, causal fill, CAGR limits, seasonal-history limits, training-only order selection, model families, SARIMAX driver requirements, final-test/calibration separation, scenario output, report archives and quoted JSON embedding in R exports.

All 21 tests passed against both the reorganized source package and a separately extracted release ZIP on the build machine. Tests use synthetic data and do not establish real-world market forecast accuracy.

## Local release checks

The release is checked by extracting its ZIP into a separate folder, running those tests again, starting its run.py with a temporary runtime directory/alternate local port, and exercising inspection, review, model execution, health/static routes, saved projects, chart evidence and downloadable exports. Generated test data does not belong in the distribution ZIP.

The extracted release passed HTTP checks for health, static assets, CSV upload, quality review, saved-project reload, SARIMA execution, chart evidence, and PDF, HTML, CSV, JSON, R Markdown, R script and evidence ZIP downloads. The downloaded evidence bundle also reran successfully as a standalone analysis using the sample CSV. An installable wheel was built offline from the package metadata. Python source/metadata, frontend JavaScript syntax and archive integrity are checked. MANIFEST.sha256 contains hashes for release files; tools/verify_release.py validates them after extraction. Extra files you generate, such as .venv and var reports, are not part of the manifest.

## Previously verified application behavior

The local application has been browser-checked for model choice, mandatory data review, interactive chart rendering, series toggles, zoom/reset, expanded dialogs, keyboard point inspection and SVG export links. HTTP checks verified saved project load, new commentary versions without numerical changes, four-market batch ZIPs and report downloads. PDF content has been checked and pages rendered for inspection.

## Unverified boundaries

- R and RStudio are unavailable here. Rmd and verification.R are generated/structurally checked, including safe data quoting, but have not been executed or knitted.
- A Docker CLI exists but lacks its Compose plugin. A Docker image build/container startup has not been executed; Docker/Compose files are starter configuration.
- The supplied GitHub Actions workflow has not run in a remote repository.
- No wheel has been installed in an entirely new OS environment unless explicitly recorded by the final release checks; source-launch testing uses the pinned installed dependencies.
- No public deployment, identity provider, database, durable queue, cloud object store or source connector is integrated.
- No live market dataset, licensed-source authenticity, country coverage, regulatory suitability or universal precision has been certified.

These boundaries prevent confusing a tested local package with a completed public commercial deployment.
