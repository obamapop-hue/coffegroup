#!/usr/bin/env bash
# Run inside the user's authenticated Google Cloud Shell. Read-only checks only.
set -euo pipefail

if [[ "${1:-}" == "--help" ]]; then
  printf 'Usage: bash scripts/gcp_preflight.sh [PROJECT_ID]\n'
  printf 'Checks project access, billing linkage and enabled APIs; creates no resources.\n'
  exit 0
fi

project_id="${1:-active-venture-449808-a5}"
if [[ ! "$project_id" =~ ^[a-z][a-z0-9-]{4,28}[a-z0-9]$ ]]; then
  printf 'Invalid project ID. Use the project ID, not its display name or number.\n' >&2
  exit 2
fi
if ! command -v gcloud >/dev/null 2>&1; then
  printf 'Open Google Cloud Shell and run this script there. gcloud is required.\n' >&2
  exit 1
fi
active_account="$(gcloud auth list --filter=status:ACTIVE --format='value(account)')"
if [[ -z "$active_account" ]]; then
  printf 'No active Google account. Finish Cloud Shell authorization first.\n' >&2
  exit 1
fi
unset active_account

printf 'Project access:\n'
gcloud projects describe "$project_id" \
  --format='json(projectId,projectNumber,lifecycleState)'

printf '\nBilling linked (True is required before deployment):\n'
gcloud billing projects describe "$project_id" --format='value(billingEnabled)'

printf '\nCurrently enabled APIs:\n'
gcloud services list --enabled --project="$project_id" --format='value(config.name)'

printf '\nRead-only checks completed. No resources created or APIs enabled.\n'
printf 'Free-trial credit balance is not verified by this script.\n'
