# Resume Normalizer

A FastAPI service that accepts a résumé, extracts structured data using OpenAI,
and stores the original file and a standardized PDF in S3.

Production URL: `https://resumes.stafflyhq.ai`

- Interactive API documentation: `/docs`
- OpenAPI schema: `/openapi.json`
- Supported uploads: PDF, DOCX, PNG, and JPEG
- Default file size limit: 25 MiB
- Capacity: one active extraction across workers sharing the extraction lock

## Client API key

Set `RESUME_NORMALIZER_API_KEY` in the calling application and send it in the
`X-API-Key` header. Store the key securely, such as in Vaultwarden, and keep it out
of Git and shared logs. Existing Caddy error logs can contain `X-Api-Key`; redact
those headers before sharing logs.

## Extract a résumé

In a Bash terminal, load the client key from Vaultwarden without storing its value
in shell history:

```bash
read -r -s -p 'Normalizer client API key: ' RESUME_NORMALIZER_API_KEY
printf '\n'

curl --fail-with-body --show-error \
  'https://resumes.stafflyhq.ai/api/v1/extract?hide_contact_info=true' \
  -H "X-API-Key: $RESUME_NORMALIZER_API_KEY" \
  -F 'file=@/path/to/resume.pdf' \
  -o normalization-result.json

unset RESUME_NORMALIZER_API_KEY
```

Replace `/path/to/resume.pdf` with your file. `-F` sends multipart form data with
the required field `file`; let curl set the multipart content type and boundary.

`hide_contact_info` defaults to `false`. Setting it to `true` hides the contact
information line in the **generated PDF**. It does not redact the original file,
`raw_text`, or structured response data.

A successful response includes:

| Field | Meaning |
| --- | --- |
| `request_id` | Identifier returned by the extraction handler |
| `status` | `success` |
| `resume_data` | Structured résumé information; see `/docs` for the schema |
| `raw_text` | Extracted text, or a marker string for vision-based extraction |
| `original_file_url` | Presigned S3 download URL for the uploaded file |
| `generated_pdf_url` | Presigned S3 download URL for the normalized PDF |
| `processing_time_ms` | Pipeline processing duration |

Presigned URLs expire (configured for 3600 seconds by default). Download and
persist the generated PDF in the calling app if it needs a durable link or email
attachment. Nonempty `raw_text` alone does not guarantee a PDF was downloaded, and
vision results use a marker rather than a complete text transcription.

## Busy responses and errors

| HTTP status | Meaning / action |
| --- | --- |
| `401` | Missing, invalid, or revoked client key; check credentials |
| `413` | Upload exceeds the configured file size limit |
| `415` | File validation failed / unsupported file type |
| `422` | Invalid request fields, insufficient extracted text, or OCR failure |
| `429` | Request rate limit exceeded; back off |
| `503` | Another extraction is active; retry after `Retry-After: 30` |
| `502` | May indicate LLM extraction failure or a proxy/upstream failure; inspect response and logs |
| `500` | Processing/server failure; inspect logs |
| `522` from Cloudflare | Origin connection timeout; check EC2 reachability and origin networking |

There is no internal job queue. Schedule busy requests for a later retry with
backoff and jitter. A client timeout does not necessarily stop server processing;
the extraction lock stays held until processing ends. Requests are not deduplicated,
so a later retry may process the same résumé again. The calling app should share
concurrency control across previews and workers and deduplicate by source revision.

See [OPERATIONS.md](OPERATIONS.md) for lock scope and memory limitations.

## EC2 deployment

The deployment uses an Ubuntu EC2 instance with a checkout at
`~/resume-normalizer`. Docker Compose builds the API on the server:

```text
Cloudflare → EC2 TCP 443 → Caddy (Cloudflare Origin Certificate) → API:8000
```

Server configuration:

- `.env.prod`: application secrets, S3 buckets, region, and database settings;
  start from [.env.prod.example](.env.prod.example) for a new installation.
- `certs/origin.pem` and `certs/origin-key.pem`: Cloudflare Origin Certificate and
  private key mounted into Caddy.
- S3 permissions: deployment supports an EC2 instance role instead of static AWS keys.
- `app_data` Docker volume: mounted at `/app/data` for the SQLite database and
  extraction lock. Preserve this volume when updating to retain client keys.
- Production Compose sets `EXTRACTION_LOCK_PATH=/app/data/extraction.lock`;
  no additional `.env.prod` change is needed for the concurrency guard.

For a new instance, [scripts/setup-server.sh](scripts/setup-server.sh) installs
Docker and outlines the checkout/configuration steps. It does not provision AWS
resources, IAM permissions, Cloudflare DNS, or the certificate files above.

To update an existing deployment after changes are pushed:

```bash
cd ~/resume-normalizer
git pull --ff-only
docker compose -f docker-compose.prod.yml up -d --build
docker compose -f docker-compose.prod.yml ps
```

If the checkout still uses the former GitHub account name, update its remote:

```bash
git remote set-url origin https://github.com/staffly-hq/resume-normalizer.git
```

Check health inside Docker and through the public domain:

```bash
docker compose -f docker-compose.prod.yml exec -T api \
  curl -fsS http://localhost:8000/api/v1/health
curl -fsS https://resumes.stafflyhq.ai/api/v1/health
```

Inspect `services.s3` as well as the HTTP status: the current health endpoint can
return `200` and `status: healthy` even when S3 is reported as `disconnected`.
Health checks do not verify OpenAI access or complete résumé extraction.

The host does not publish API port 8000 in production. The existing
`scripts/deploy.sh` still checks host `localhost:8000`; use the container health
command above instead of relying on that script's final check.

For troubleshooting:

```bash
docker compose -f docker-compose.prod.yml logs --since=10m --tail=100
free -h
sudo journalctl -k -b -1 --no-pager | grep -Ei 'out of memory|oom|killed process'
```

The last command checks the previous boot if its journal is retained. Small
instances can still run out of RAM on a single complex input; admission control
reduces overlap but is not a hard memory limit.

## Local development

With Docker and Docker Compose installed:

```bash
cp .env.example .env
# Edit .env with the required service configuration.
docker compose up -d --build
curl -fsS http://localhost:8000/api/v1/health
```

The development stack starts LocalStack for S3. Open `http://localhost:8000/docs`.
OpenAI calls still use the configured external service. The development API database is not mounted on a
persistent volume, so recreating its container can discard development client keys.
