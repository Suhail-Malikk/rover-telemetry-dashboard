"""Server-only, read-only Supabase REST access. Never send credentials to widgets."""
from datetime import datetime, timedelta, timezone
from urllib.parse import urlparse
import requests

class DataError(Exception):
    pass

def validate_connection(url, key):
    parts = urlparse(url)
    if parts.scheme != "https" or not (parts.hostname or "").endswith(".supabase.co"):
        raise DataError("SUPABASE_URL must be your HTTPS project URL ending in .supabase.co.")
    if parts.username or parts.password or parts.query or parts.fragment:
        raise DataError("Use the project URL without credentials, query parameters or fragments.")
    if not key or key == "PASTE_SERVER_KEY_HERE":
        raise DataError("Add SUPABASE_KEY in Streamlit Secrets.")
    if key.startswith("sb_publishable_"):
        raise DataError("Use a server secret or legacy service_role key; this table is not publicly readable.")

def api_headers(key):
    headers = {"apikey": key, "Accept": "application/json"}
    if not key.startswith("sb_secret_"):
        headers["Authorization"] = "Bearer " + key
    return headers

def read_rows(url, key, params):
    validate_connection(url, key)
    try:
        response = requests.get(url.rstrip("/") + "/rest/v1/telemetry",
                                headers=api_headers(key), params=params, timeout=(3, 6))
    except requests.RequestException:
        raise DataError("Supabase could not be reached. Check the connection; the last readings remain visible.") from None
    if response.status_code in (401, 403):
        raise DataError("Read access denied. Check the server key and service_role SELECT permission on telemetry.")
    if response.status_code == 404:
        raise DataError("Telemetry table not found. Check the project URL and database setup.")
    if response.status_code != 200:
        raise DataError(f"Supabase returned HTTP {response.status_code}. Retry shortly.")
    try:
        rows = response.json()
    except ValueError:
        raise DataError("Supabase returned an invalid response.") from None
    if not isinstance(rows, list) or any(not isinstance(x, dict) for x in rows):
        raise DataError("Supabase returned an unexpected response.")
    return rows

def poll_rows(url, key, device, cursor=None):
    params = {"select": "*", "device_id": "eq." + device}
    if cursor:
        # Overlap handles boundary ties and retries; merge by primary key.
        start = datetime.fromisoformat(cursor.replace("Z", "+00:00")) - timedelta(seconds=2)
        params.update(received_at="gte." + start.isoformat(),
                      order="received_at.asc,session_id.asc,packet_number.asc", limit=1000)
    else:
        params.update(order="received_at.desc,session_id.desc,packet_number.desc", limit=900)
    return read_rows(url, key, params)

def history_rows(url, key, device, start, end, max_rows=10000):
    rows, offset = [], 0
    # Fixed end bounds keep paging stable while new uploads continue.
    while len(rows) < max_rows:
        page = read_rows(url, key, {
            "select": "*", "device_id": "eq." + device,
            "and": f"(received_at.gte.{start.isoformat()},received_at.lte.{end.isoformat()})",
            "order": "received_at.asc,session_id.asc,packet_number.asc",
            "limit": min(1000, max_rows - len(rows)), "offset": offset,
        })
        rows.extend(page)
        offset += len(page)
        if len(page) < 1000:
            return rows, False
    # Report cap conservatively; don't claim a complete export at the limit.
    return rows, True
