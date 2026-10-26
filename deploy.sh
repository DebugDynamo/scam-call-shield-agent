#!/usr/bin/env bash
set -euo pipefail

# Single-line GCP Cloud Run deployment targeting Mumbai (asia-south1)
gcloud run deploy scam-call-shield --source . --region asia-south1 --platform managed --allow-unauthenticated --port 8080 --set-env-vars="GEMINI_MODEL=${GEMINI_MODEL:-gemini-3.8-flash},LLM_PROVIDER=${LLM_PROVIDER:-gemini}"
