# Deployment guide and market-readiness roadmap

## Three distinct deployment levels

| Level | Status | Intended use |
|---|---|---|
| Native local application | Implemented and tested | One analyst on one computer; loopback browser access |
| Local container | Dockerfile/Compose starter supplied; image build not tested | Reproducible local runtime with loopback host port and persistent volume |
| Public multi-user service | Proposed architecture; not implemented | Authenticated teams/tenants, durable jobs, managed storage and operations |

A tidy repository, Dockerfile or ZIP does not make a research server public-market ready. Complete the gates below before inviting external users.

## Native local configuration

Use the root README beginner commands. Python 3.12, pip and the requirements are the only mandatory runtime tools. R/RStudio and Pandoc are optional for rendering exported Rmd files. Node.js is not needed to run the dashboard; JavaScript executes in the browser.

| Setting | Default | Effect |
|---|---|---|
| BI_PORT / --port | 8000 | HTTP service port; browser Host/Origin checks use the same port |
| BI_BIND_HOST / --host | 127.0.0.1 | Listening address; keep loopback for native use |
| BI_DATA_DIR | Project var/ for run.py; working-directory var/ for installed entry point | Writable runtime storage directory |

CLI flags override port/host environment defaults. run.py honors an already-set BI_DATA_DIR. Native launch does not load .env. Example macOS/Linux custom data location:

```sh
export BI_DATA_DIR="$PWD/var"
python run.py --port 8001
```

Windows PowerShell:

```powershell
$env:BI_DATA_DIR = Join-Path (Get-Location) "var"
.\.venv\Scripts\python.exe run.py --port 8001
```

No OpenAI or other service credential is required. Bind address 0.0.0.0 is used only inside the provided container configuration; the Compose port publishes to host loopback. Host/Origin checks still accept only localhost/127.0.0.1 with the actual service port. A domain-based deployment requires a deliberate production-origin/identity design rather than disabling these checks.

## Docker starter

Install Docker and its Compose plugin. From the extracted project root:

```sh
docker compose up --build
```

Then open localhost:8000. This downloads a Python base image and packages, so the first build needs Internet access. Forecasting still uses local compute. A named volume `bi-data` stores /app/var inside the container; it is separate from your native project var folder.

The image runs as UID/GID 10001 and includes a /healthz liveness probe. This probe confirms the HTTP service responds, not that models or source data are accurate. An initial image build/container execution has not been tested here; the build machine's Docker CLI lacks the Compose plugin. Configuration syntax is also inspectable in compose.yaml and deploy/Dockerfile.

To choose another local port, copy .env.example to .env and change BI_PORT to 8001, or set BI_PORT in your shell. Compose passes the same port internally and externally, which keeps the local Host/Origin checks consistent. Open localhost:8001. The Compose service fixes BI_BIND_HOST and BI_DATA_DIR internally; corresponding native .env comments are explanatory and do not override those service values.

```sh
docker compose logs -f dashboard
docker compose down
```

`down` stops/removes the service but retains its named data volume. Back up the volume before replacing infrastructure. Do not use volume-deletion flags unless you deliberately intend to remove saved inputs and reports. Backup location and restore procedures depend on your Docker platform.

## Proposed commercial deployment components

The following is an engineering proposal, not a list of current integrations or a requirement to buy specific providers.

| Concern | Suggested category/example | Integration work | Release gate |
|---|---|---|---|
| Web/API | FastAPI with Uvicorn, or another maintained production stack | Move routes into typed request/response schemas; version API; explicit limits, errors and origin policy | Contract, malformed-input and rate-limit tests pass |
| Identity | OIDC provider; self-hosted or managed | Login/session lifecycle, roles and authorization on every dataset/job/report route | Cross-tenant access tests pass |
| Metadata | PostgreSQL with migrations | User/tenant/dataset/version/job/model/report schema; transactions and relationships | Restart and migration recovery tested |
| Source/report objects | Private S3-compatible object storage | Scoped paths, checksums, encryption, signed/scoped downloads, retention and deletion | Unauthorized access denied; restore verified |
| Work execution | Durable queue such as Celery plus Redis, or a managed queue | Replace daemon threads; idempotent jobs, cancellation, time/memory limits, retries and failure states | Worker kill/restart does not lose completed jobs or duplicate publication |
| Model worker | Isolated forecast process/container | Resource quotas, environment versions and artifact publication; no arbitrary user code | Oversized/malformed inputs cannot exhaust the service |
| HTTPS/edge | Domain and TLS reverse proxy such as Caddy/Nginx or managed ingress | Certificates, allowed origins, secure sessions and upload body/time limits | TLS and renewal verified |
| Live status | Authenticated polling or SSE | User-scoped status, reconnect behavior and consistent durable state | Reconnect displays correct persisted progress |
| Market sources | Licensed/public source adapters, file/cloud storage connectors | Explicit permission, credentials, metadata, versioning, refresh schedule, units and revision reconciliation | Provenance and source rights recorded for every refresh |
| Operations | Structured logs, metrics/traces, error tracking and alerts | Latency, job duration/failure, memory, queue depth and coverage drift; redact source values/secrets | Operational failure triggers useful alerts |
| Delivery | Container registry, CI/CD and managed runtime | Automated checks, image/dependency scanning, staged rollout and rollback | Staging exercise and rollback pass |
| Backups | Database/object-store backup jobs | Retention policy, restore drills and checksum verification | Tested recovery meets chosen targets |
| Secrets | Platform secrets manager | Scoped connector credentials and rotation; no keys in frontend or ZIP | Secret scan and permission review pass |
| Compliance/product | Requirements appropriate to intended customers | Data rights, privacy/retention terms, accessibility, localization and approval workflow | Customer/legal/security acceptance criteria agreed and verified |

The architecture must first replace the process-memory dictionaries. Adding multiple server workers now would split state and break job routing. Current report writes also need atomic commit/versioning; merely moving the folders to a shared mount does not solve authorization or job durability.

Deployment considerations such as HTTPS, startup, restarts, worker memory and replication are described in [FastAPI deployment concepts](https://fastapi.tiangolo.com/deployment/concepts/). Compose can express local service configuration as documented by [Docker](https://docs.docker.com/compose/). These references inform the proposal; they do not imply those tools are integrated into this Python-stdlib service.

## Source synchronization to implement

Current behavior is manual upload and saved snapshot reload. Proposed automatic synchronization should:

1. Store connector identity, source URL/id, permission scope, licensing notes and refresh policy.
2. Fetch into a versioned raw object; compute its hash and record timestamps/schema/units.
3. Compare with the prior source version. Record revisions rather than silently replacing old observations.
4. Profile and review changed schema, missing data, units or geographic boundaries.
5. Require an analyst review when changes affect the model definition or aggregation.
6. Enqueue a new versioned analysis; do not modify an already-issued report in place.
7. Publish results only after artifact integrity checks and authorization rules pass.
8. Notify authorized users of changed findings or failures; retain old model/source/report relationships.

No connector/scheduler/live external source has been shipped. A source citation currently never initiates a fetch. Public market figures may be revised and vendor estimates may not be observed outcomes; the integration must keep those distinctions.

## Forecasting improvements to prioritize

- Larger real-data benchmark sets with rolling-origin experiments across horizons and segments.
- Better horizon-specific/dependent-series interval calibration with explicit uncertainty evaluation.
- Structural-break/change-point tests and governed regime comparisons rather than descriptive screening alone.
- Realtime SARIMAX driver scenarios that respect driver availability at prediction time, plus uncertainty propagation.
- FX/deflator adjustments, weighted rates, total/component reconciliation and geographic/sector ontology handling.
- Hierarchical forecasts with coherent aggregate/segment totals when business definitions permit it.
- Model/stationarity diagnostics, optimizer robustness, sensitivity analysis and drift monitoring.
- R execution CI or an optional controlled R worker to test Rmd outputs; never run arbitrary uploads unsandboxed.
- Collaborative report review, annotations, approval/version lineage and stronger evidence citation rendering.

Do not add more model names purely for coverage. Establish whether each new method beats simple baselines on the intended data, and expose unsupported/failed cases.

## Suggested implementation order

1. Harden input schemas, artifact integrity, storage/versioning and tests.
2. Add identity, tenant authorization and scoped metadata/object storage.
3. Replace daemon threads with a durable queue and isolated forecast workers.
4. Add production HTTPS/process management, monitoring, backups and staged delivery.
5. Integrate selected licensed sources with provenance and guarded refresh/review.
6. Benchmark methods and uncertainty on representative customer data; add model governance.
7. Add collaboration, accessibility/localization and support workflows; pilot with clearly documented limits.

Hosting is not performed by this ZIP. Choose deployment providers only after deciding expected users, data sensitivity, storage, latency, support and budget. No cloud price or capacity estimate is assumed.
