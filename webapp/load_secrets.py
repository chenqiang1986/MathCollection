"""Print `export KEY=VALUE` lines for any expected secret env vars that
are not already set in the environment, fetching values from Google Cloud
Secret Manager. Intended to be eval'd by entrypoint.sh:

    eval "$(python /app/webapp/load_secrets.py)"

Auth uses Application Default Credentials, which on GKE picks up Workload
Identity and on Cloud Run picks up the runtime service account.
"""

import os
import shlex

SECRET_KEYS = ("GOOGLE_CLIENT_ID", "GOOGLE_CLIENT_SECRET", "ANTHROPIC_API_KEY")


def main() -> None:
    project = os.environ.get("GCP_PROJECT_ID")
    if not project:
        return
    missing = [k for k in SECRET_KEYS if not os.environ.get(k)]
    if not missing:
        return

    from google.cloud import secretmanager

    client = secretmanager.SecretManagerServiceClient()
    prefix = os.environ.get("SECRET_PREFIX", "MATH_MISTAKE_TRACKER__")
    for key in missing:
        name = f"projects/{project}/secrets/{prefix}{key}/versions/latest"
        value = client.access_secret_version(name=name).payload.data.decode()
        print(f"export {key}={shlex.quote(value)}")


if __name__ == "__main__":
    main()
