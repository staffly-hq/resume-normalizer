# Extraction capacity

The normalizer admits one active extraction at a time. Additional authenticated
extraction requests receive HTTP 503 with `Retry-After: 30`. Callers should schedule
a retry with backoff and jitter, rather than retry immediately. Health checks and
key management remain available while extraction is running.

The lock is held by the synchronous processing thread through file reading,
processing, uploads, and response construction. A client timeout/disconnect does
not release it while processing continues. Errors release it; worker termination
releases it through the OS. This does not deduplicate requests: a retry arriving
after completion can process the same file again.

`EXTRACTION_LOCK_PATH` defaults to `/tmp/resume-normalizer-extraction.lock`.
Production Compose sets it to `/app/data/extraction.lock` on the existing shared
volume. Every worker/container on the host must use the same file and filesystem.
Do not delete or replace this file while workers are running. Separate hosts with
separate volumes do not share this limit; use distributed admission control before
scaling across hosts. The lock uses Unix `flock` (the production image is Linux).

Admission happens after FastAPI parses multipart input and resolves dependencies,
but before the handler reads the complete file into memory or starts extraction.
This bounds active extraction, not incoming uploads. It is not a hard memory cap:
a single complex input can still exceed a small instance's RAM. Scanned PDFs are
rendered to temporary PNG files with a 2000-pixel maximum edge instead of retaining
all uncompressed pages in RAM. Compressed pages are still collected for vision.

# Deploy and check

After updating the checkout on the server:

```sh
docker compose -f docker-compose.prod.yml up -d --build
docker compose -f docker-compose.prod.yml ps
curl --fail --show-error https://resumes.stafflyhq.ai/api/v1/health
```

Validate a real résumé upload and check PDF quality after deployment. During a
long extraction, another extraction should return 503 with `Retry-After`, while
health checks remain responsive. Callers must treat 503 as pending/retryable.

Review memory usage under real inputs before increasing concurrency. No instance
resize, swap change, key rotation, or remote deployment is performed by this code.
