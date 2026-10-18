# Deploying the Clefs API

This page moved to **[docs/DEPLOY.md](../docs/DEPLOY.md)**, which covers the whole deployment (Vercel + Neon, Google Cloud Run, environment variables, WhatsApp, Google Gemini and the Live API phone path, vendors, rollout order).

Quick pointers:

- Vercel config: `server/vercel.json` (one Python function pinned to `sin1`, `.env` files excluded from the bundle).
- Job tick: Vercel Cron calls `/internal/cron/tick` every minute on Pro. Hobby plans cannot run a per-minute cron: drop the `crons` block and use an external pinger (cron-job.org or an uptime monitor) that calls the same route with `Authorization: Bearer <CRON_SECRET>`. Details in docs/DEPLOY.md section 6.
- Local runs: always `export DATABASE_URL=postgresql://localhost/clefs_dev` and `export NOTIFY_MODE=log SMS_ENABLED=false`; `server/.env` may hold production values. Details in docs/DEPLOY.md section 18.
- Docker Compose (UAE-resident option): docs/DEPLOY.md section 19.

## Google Cloud Run

Build and deploy the API from this folder's `Dockerfile` (docs/DEPLOY.md section 20 has the full list):

```bash
cd server
gcloud run deploy clefs-api --source . --region asia-southeast1 --allow-unauthenticated \
  --set-env-vars ENV=production,GOOGLE_GENAI_USE_VERTEXAI=true,GOOGLE_CLOUD_PROJECT=<project>,GOOGLE_CLOUD_LOCATION=asia-southeast1 \
  --set-secrets DATABASE_URL=clefs-database-url:latest,DATABASE_URL_UNPOOLED=clefs-database-url-unpooled:latest,SECRET_KEY=clefs-secret-key:latest,CRON_SECRET=clefs-cron-secret:latest
```

- AI: `GEMINI_API_KEY` (as a secret), or Vertex AI with `GOOGLE_GENAI_USE_VERTEXAI=true` + `GOOGLE_CLOUD_PROJECT` + `GOOGLE_CLOUD_LOCATION` and `roles/aiplatform.user` on the service account.
- Database and the rest: `DATABASE_URL`, `DATABASE_URL_UNPOOLED`, `SECRET_KEY`, `CRON_SECRET`, vendor keys, as in docs/DEPLOY.md section 5.
- Job tick: a Cloud Scheduler HTTP job calls `/internal/cron/tick` every minute with `Authorization: Bearer <CRON_SECRET>`.
