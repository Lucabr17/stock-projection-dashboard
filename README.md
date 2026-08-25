# Stock Projection Dashboard — Python + Streamlit

A responsive EPS-based stock price projection dashboard designed to run locally or on Streamlit Community Cloud from a GitHub repository.

## Features
- Search ticker/company with Yahoo Finance autocomplete.
- Current price, TTM EPS and current P/E.
- Historical 1Y / 3Y / 5Y Revenue and EPS CAGR when Yahoo annual statements contain enough data.
- Editable Starting EPS, Growth Rate and Future P/E.
- 1–10 year projection horizon, default 5 years.
- Projected EPS, Projected Price, Annual Return and Total Return.
- Interactive Plotly trajectory chart with yearly price badges.
- Saved Projections.
- Local SQLite persistence for local runs.
- Optional Google Sheets persistence for Streamlit Cloud, so saved projections survive restarts and are available from every device.

## Run locally

```bash
python -m venv .venv
source .venv/bin/activate        # macOS/Linux
# .venv\\Scripts\\activate       # Windows
pip install -r requirements.txt
streamlit run app.py
```

## Deploy with GitHub + Streamlit Community Cloud

1. Create a GitHub repository and upload `app.py`, `requirements.txt` and `.gitignore`.
2. In Streamlit Community Cloud, create a new app and select the GitHub repo, branch and `app.py`.
3. Deploy.

### Important: persistent saved projections

Streamlit Cloud instances are not a reliable place to keep a writable local SQLite file permanently. The app therefore supports Google Sheets as the durable shared storage layer.

To enable it:

1. Create a Google Cloud service account and a Google Sheet.
2. Share the Sheet with the service-account email as Editor.
3. In Streamlit Cloud, open **App settings → Secrets** and add:

```toml
spreadsheet_id = "YOUR_GOOGLE_SHEET_ID"
worksheet = "Projections"

[gcp_service_account]
type = "service_account"
project_id = "YOUR_PROJECT_ID"
private_key_id = "YOUR_PRIVATE_KEY_ID"
private_key = "-----BEGIN PRIVATE KEY-----\\nYOUR_KEY\\n-----END PRIVATE KEY-----\\n"
client_email = "YOUR_SERVICE_ACCOUNT_EMAIL"
client_id = "YOUR_CLIENT_ID"
auth_uri = "https://accounts.google.com/o/oauth2/auth"
token_uri = "https://oauth2.googleapis.com/token"
auth_provider_x509_cert_url = "https://www.googleapis.com/oauth2/v1/certs"
client_x509_cert_url = "YOUR_CERT_URL"
```

The first save creates the `Projections` header row if necessary. Subsequent saves are shared across devices/users of that deployed app.

## Notes
- Yahoo Finance data can be delayed, incomplete or unavailable for some tickers. The app handles missing CAGR periods as `—`.
- For a multi-user production app, add authentication and a database such as Supabase/Postgres rather than exposing a shared Google Sheet to every user.
