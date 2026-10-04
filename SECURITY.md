# Security and deployment boundary

This release is a local, single-user research application. The native launcher binds to loopback. Docker Compose publishes its port only on host loopback. There is no account system, tenant isolation, authorization, encrypted storage, durable worker queue or public-facing abuse protection.

Host and POST Origin checks restrict requests to the local origin. Upload limits and static SVG-shape validation reduce exposure; they are not a complete production security system. Saved projects contain original uploaded bytes; evidence reports contain selected observations and analyst notes. Protect and back up the data directory.

Before public deployment, implement identity and authorization, per-tenant storage, strict schemas and upload inspection, HTTPS, job quotas/timeouts, audit logs, backup/restore, dependency scanning, retention/deletion and privacy review. Changing the bind address is insufficient. See docs/DEPLOYMENT.md for the proposed architecture.

No credentials, API keys, actual user uploads or previous reports are shipped. Do not commit .env or generated runtime folders. If adding data connectors, use a secrets manager and scoped credentials. Do not execute arbitrary uploaded R/Python code on the application server; R exports are intended for local inspection.
