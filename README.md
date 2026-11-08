# ROVER — Power telemetry dashboard

A Streamlit dashboard for the existing ESP A → Supabase telemetry pipeline. The app reads `public.telemetry`; it does not change firmware or write database rows.

## Deploy on Streamlit Community Cloud

1. Extract this ZIP on your computer.
2. Create a new GitHub repository named `rover-telemetry-dashboard`. Use a private repository if you want the source private. Upload the **contents** of the extracted folder, not the ZIP. `app.py` and `requirements.txt` must be at the repository root. Include `.streamlit/config.toml` to retain the light theme. Leave `secrets.example.toml` as a placeholder; never add a real key to it.
3. Open https://share.streamlit.io/ and sign in with GitHub. Select **Create app**, then deploy from an existing repository. Select this repository, branch `main`, and main file `app.py`.
4. Open **Advanced settings**. Choose Python **3.12**. Paste the contents of `secrets.example.toml` into **Secrets** and replace only `PASTE_SERVER_KEY_HERE` with your Supabase server key.
5. Find that key in the Supabase project's **Settings → API Keys**: use an `sb_secret_...` secret key or the legacy `service_role` key. The publishable/anon key and ESP upload `DEVICE_API_KEY` are different credentials and will not work with your current read permissions. Keep the server key in Streamlit Secrets only; do not put it in GitHub, a screenshot, or chat.
6. Save the settings and select **Deploy**. Choose private app access if you do not want other people viewing the readings. A private repository alone is not a guarantee of private app access; review Streamlit's app sharing settings.
7. Open **Connection → Check database access** in the deployed app. Then return to **Live overview**, power both ESPs, and check that the packet number advances. If your firmware uses a different device ID, change `DEVICE_ID` in Secrets to match exactly.

The project URL is already supplied. No new SQL is required with the existing telemetry table and its service_role SELECT grant.

## Included views

- Live overview: stack voltage, signed sensor 1 current, spread across valid configured cell values, and packet number.
- Six cell slots and eight current sensor slots; default four configured cells and sensor 1. Unconnected channels show no numeric value.
- Cell/stack voltage and current trends, with 1, 5, or 15 minute windows.
- Live, offline, delayed, unsynchronised-clock and read-error states.
- Adjustable current magnitude threshold and hold time. Alerts start disabled; set the threshold after testing your motor. The initial 2 A field is only a starting UI setting, not a validated motor limit.
- Packet gaps, BMS CRC rejects, and upload-buffer drops.
- History lookup and CSV export for 15 minutes, 1 hour, 6 hours, or 24 hours.

The dashboard displays received cell and stack fields as stored. It does not recalculate the stack or generate missing channels. It uses the validity flags to suppress invalid readings. Voltage bars are visual voltage scales, not state-of-charge estimates.

## Behaviour and limits

- Live overview requests updates about once per second while the page is open. Network latency can slow refresh. This does not change the ESP upload interval.
- History loads only when requested. Initial live load contains up to 900 recent rows; the browser session retains up to 3,600. Charts show the selected window among loaded rows.
- CSV export is capped at 10,000 rows per load and displays a warning at the cap. Choose a shorter period for a complete export. CSV timestamps remain UTC; the live UI uses Asia/Kolkata by default.
- Missing packet counts include only internal gaps between observed packets within each device restart session. They do not count packets before the loaded window or confuse packet reset with loss. CRC and buffer-drop counters come from the ESP and reset when it restarts.
- Live status requires recent upload AND a recent `recorded_at` time. Buffered historical uploads do not become live measurements merely because they arrived recently. Without an ESP clock timestamp, the app says Time not synced.
- Current alerts use absolute signed current, valid readings, and consecutive packets spanning the chosen hold time. Invalid data, gaps, stale data, or a restart break that sequence. One-second telemetry cannot detect every short current spike.
- Alerts are visible dashboard warnings, not motor shutoff or a background notification service. They operate while Live overview is open. Settings apply to the current browser session.
- This is a server-side read-only client. Its supplied server key is privileged; only trusted developers should have access to app Secrets or deploy code. The key is never sent to browser widgets.

## Local run (optional, Windows)

Install Python 3.12. Open a terminal inside the extracted folder:

```powershell
py -3.12 -m venv .venv
.venv\Scripts\python -m pip install -r requirements.txt
Copy-Item secrets.example.toml .streamlit\secrets.toml
```

Edit `.streamlit/secrets.toml` locally and add the key. Then run:

```powershell
.venv\Scripts\python -m streamlit run app.py
```

On later runs only the last command is needed. `.gitignore` excludes the real secrets file and virtual environment.

## Troubleshooting

| Symptom | Check |
| --- | --- |
| Add your Supabase server key | Add SUPABASE_KEY in the app's Secrets and save. |
| Read access denied | Use this project's server secret or legacy service_role key. The table must grant SELECT to service_role. |
| Read access works, zero rows | Match DEVICE_ID with the firmware; the default is esp-a, not esp-a-test. |
| Offline | No recent rows are reaching the database for this device. Check the ESP Serial Monitor and hotspot. |
| Delayed data | The ESP is uploading older buffered samples. Wait for its queue to drain. |
| Time not synced | Check the ESP's NTP time synchronization over the hotspot. |
| Needs zero / blank current | Motor off, send z in the ESP Serial Monitor, then test the sensor. |
| ADC range error | Check sensor power, ground and output wiring before calibration. |
| Blank cell | Inspect the BMS validity mask and firmware readings. The GUI does not replace invalid values with zero. |
| Plain/dark appearance | Confirm `.streamlit/config.toml` was uploaded at the repo root. Use the app's light theme. |

## Verification

Checked with Python 3.12 and the versions in requirements.txt. Six automated tests cover validity, timestamps, gap detection across restarts, alert persistence and sign, API errors and headers, history paging, and Streamlit navigation/alert/history rendering. Desktop layout was inspected in a browser. Actual access to your private Supabase project must be verified after adding your key.

To rerun tests locally:

```powershell
.venv\Scripts\python -m pip install pytest==8.4.2
.venv\Scripts\python -m pytest tests -q
```

## Official deployment documentation

- https://docs.streamlit.io/deploy/streamlit-community-cloud/deploy-your-app
- https://docs.streamlit.io/deploy/streamlit-community-cloud/deploy-your-app/secrets-management
