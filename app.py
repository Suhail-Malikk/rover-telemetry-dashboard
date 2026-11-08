from datetime import datetime, timedelta, timezone
from html import escape
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError
import os
import pandas as pd
import plotly.graph_objects as go
import streamlit as st
from data import DataError, poll_rows, history_rows, validate_connection
from model import (timestamp, finite, merge_rows, latest_row, data_age, link_state,
                   bms_fresh, cell_value, stack_value, current_value, packet_gaps,
                   overcurrent_channels)

st.set_page_config(page_title="Rover | Power telemetry", page_icon="◈", layout="wide")
st.markdown("<style>" + Path(__file__).with_name("styles.css").read_text() + "</style>", unsafe_allow_html=True)

def setting(name, fallback=None):
    try:
        return st.secrets.get(name, os.environ.get(name, fallback))
    except (FileNotFoundError, st.errors.StreamlitSecretNotFoundError):
        return os.environ.get(name, fallback)

URL = str(setting("SUPABASE_URL", "https://vwgxaaetbxbkpbmwfeha.supabase.co"))
KEY = str(setting("SUPABASE_KEY", ""))
DEVICE = str(setting("DEVICE_ID", "esp-a"))
try:
    TZ = ZoneInfo(str(setting("DISPLAY_TIMEZONE", "Asia/Kolkata")))
except ZoneInfoNotFoundError:
    TZ = ZoneInfo("UTC")
try:
    CELL_COUNT = min(6, max(1, int(setting("ACTIVE_CELL_COUNT", 4))))
    ACTIVE_CURRENTS = sorted({int(x) for x in setting("ACTIVE_CURRENT_CHANNELS", [1]) if 1 <= int(x) <= 8})
except (ValueError, TypeError):
    CELL_COUNT, ACTIVE_CURRENTS = 4, [1]
CONFIGURED = bool(KEY and KEY != "PASTE_SERVER_KEY_HERE")

@st.cache_data(ttl=1, show_spinner=False, max_entries=16)
def poll_cached(url, key, device, cursor):
    return poll_rows(url, key, device, cursor)

with st.sidebar:
    st.markdown('<div class="logo"><span class="logo-icon">∿</span> ROVER</div><div class="side-kicker">POWER TELEMETRY</div>', unsafe_allow_html=True)
    page = st.radio("Workspace", ["Live overview", "History & export", "Connection"], label_visibility="collapsed")
    st.divider()
    st.markdown("### Display")
    live_enabled = st.toggle("Live refresh · 1 second", value=True)
    window_minutes = st.select_slider("Chart window", options=[1, 5, 15], value=5, format_func=lambda n: f"{n} min")
    voltage_plot = st.selectbox("Voltage chart", ["Cell voltages", "Stack voltage"])
    st.divider()
    st.markdown("### Current alert")
    alert_enabled = st.toggle("Enable threshold alert", value=False)
    threshold = st.number_input("Current magnitude limit (A)", min_value=0.01, max_value=100.0, value=2.0, step=0.1, format="%.2f", disabled=not alert_enabled)
    hold_seconds = st.select_slider("Hold time", options=[1, 2, 3, 5], value=1, format_func=lambda n: f"{n} s", disabled=not alert_enabled)
    st.caption("Uses absolute current. Set the limit after motor testing. Changes apply to this browser session.")
    st.divider()
    st.markdown(f'<div class="side-note">DEVICE &nbsp; <b>{escape(DEVICE)}</b><br>Refresh target &nbsp; 1 Hz<br>Time zone &nbsp; {escape(str(TZ))}<br>Read-only monitoring</div>', unsafe_allow_html=True)

if st.session_state.get("source_device") != DEVICE:
    st.session_state["source_device"] = DEVICE
    st.session_state["telemetry_rows"] = []
    st.session_state["cursor"] = None
    st.session_state["query_error"] = ""
    st.session_state["last_poll"] = None


def fmt(value, places=3):
    return f"{value:.{places}f}" if finite(value) else "—"


def heading(title, subtitle):
    st.markdown(f'<div class="page-head"><div><div class="eyebrow">MISSION CONTROL / ELECTRICAL SYSTEMS</div><div class="page-title">{escape(title)}</div><div class="page-sub">{escape(subtitle)}</div></div><div class="device-badge">◉ &nbsp; {escape(DEVICE)} &nbsp; / &nbsp; SUPABASE</div></div>', unsafe_allow_html=True)


def time_label(value):
    t = timestamp(value)
    return t.astimezone(TZ).strftime("%H:%M:%S") if t else "—"


def summary_card(label, value, unit, note, accent=False):
    return f'<div class="summary {"accent" if accent else ""}"><div class="metric-label">{escape(label)}</div><div class="reading">{escape(str(value))}<span class="unit">{escape(unit)}</span></div><div class="metric-note">{escape(note)}</div></div>'


def channel_tile(label, value, unit, state, note, inactive=False, alarm=False, voltage=False):
    classes = "tile" + (" inactive" if inactive else "") + (" alarm" if alarm else "")
    bar = ""
    if voltage and not inactive:
        width = min(100, max(0, value / 4.3 * 100)) if finite(value) else 0
        bar = f'<div class="volt-track"><div class="volt-fill" style="width:{width:.1f}%"></div></div>'
    return f'<div class="{classes}"><div class="tile-top">{escape(label)}<span class="dot {state}"></span></div><div class="tile-value">{fmt(value)}<span class="unit">{unit}</span></div><div class="tile-foot">{escape(note)}</div>{bar}</div>'


def chart(rows, kind):
    fig = go.Figure()
    if kind == "current":
        fields = [(i, f"Sensor {i}", current_value) for i in ACTIVE_CURRENTS]
        colors = ["#0c9489", "#536dce", "#da9b45", "#9974c4"]
        title, unit = "Motor current", "A"
    elif kind == "stack":
        fields, colors = [(0, "Stack", lambda r, _: stack_value(r))], ["#087f8c"]
        title, unit = "Stack voltage", "V"
    else:
        fields = [(i, f"Cell {i}", cell_value) for i in range(1, CELL_COUNT + 1)]
        colors = ["#0c9489", "#536dce", "#da9b45", "#9974c4", "#d77f91", "#6a9daf"]
        title, unit = "Cell voltages", "V"
    ordered = sorted(rows, key=lambda r: timestamp(r.get("recorded_at")) or timestamp(r.get("received_at")) or datetime.min.replace(tzinfo=timezone.utc))
    any_value = False
    for pos, (index, name, getter) in enumerate(fields):
        xs, ys, prev_t, prev_session = [], [], None, None
        for row in ordered:
            t = timestamp(row.get("recorded_at")) or timestamp(row.get("received_at"))
            if not t:
                continue
            if prev_t and ((t - prev_t).total_seconds() > 3 or prev_session != row.get("session_id")):
                xs.append(t.astimezone(TZ)); ys.append(None)
            value = getter(row, index)
            any_value |= value is not None
            xs.append(t.astimezone(TZ)); ys.append(value)
            prev_t, prev_session = t, row.get("session_id")
        fig.add_trace(go.Scatter(x=xs, y=ys, name=name, mode="lines", connectgaps=False,
                                line=dict(color=colors[pos % len(colors)], width=2),
                                hovertemplate=f"%{{x|%H:%M:%S}}<br>{name}: %{{y:.3f}} {unit}<extra></extra>"))
    if kind == "current" and alert_enabled:
        for limit in [threshold, -threshold]:
            fig.add_hline(y=limit, line_color="#d46a6a", line_dash="dot", line_width=1)
    if not any_value:
        fig.add_annotation(text="Waiting for valid readings", x=.5, y=.5, xref="paper", yref="paper", showarrow=False, font=dict(color="#9aa8b9", size=12))
    fig.update_layout(height=242, margin=dict(l=18, r=18, t=47, b=15),
                      paper_bgcolor="white", plot_bgcolor="white", font=dict(family="DM Sans, sans-serif", color="#8090a4", size=10),
                      title=dict(text=title, font=dict(family="Space Grotesk, sans-serif", color="#253b53", size=15), x=.035),
                      legend=dict(orientation="h", y=1.15, x=1, xanchor="right", font=dict(size=9)),
                      hovermode="x unified", uirevision=kind)
    fig.update_xaxes(showgrid=False, tickformat="%H:%M:%S", zeroline=False)
    fig.update_yaxes(gridcolor="#f0f3f7", zeroline=False, title=dict(text=unit), tickformat=".2f")
    return fig


@st.fragment(run_every="1s")
def overview():
    now = datetime.now(timezone.utc)
    if CONFIGURED and live_enabled:
        try:
            rows = poll_cached(URL, KEY, DEVICE, st.session_state.get("cursor"))
            merged = merge_rows(st.session_state.get("telemetry_rows", []), rows)
            st.session_state["telemetry_rows"] = merged
            if merged:
                newest = max(timestamp(r.get("received_at")) for r in merged if timestamp(r.get("received_at")))
                st.session_state["cursor"] = newest.isoformat()
            st.session_state["query_error"] = ""
            st.session_state["last_poll"] = now
        except DataError as error:
            st.session_state["query_error"] = str(error)
    rows = st.session_state.get("telemetry_rows", [])
    row = latest_row(rows)
    error = st.session_state.get("query_error", "")
    state, tone = link_state(row, now, bool(error))
    if not live_enabled:
        state, tone = "Paused", "muted"
    visible = [r for r in rows if (timestamp(r.get("received_at")) or datetime.min.replace(tzinfo=timezone.utc)) >= now - timedelta(minutes=window_minutes)]
    active_alerts = overcurrent_channels(rows, row, now, ACTIVE_CURRENTS, threshold, hold_seconds, alert_enabled) if not error and live_enabled else []
    gaps = packet_gaps(visible)
    fresh_bms = bms_fresh(row, now)
    source_age = data_age(row, now)
    values = [cell_value(row, i) for i in range(1, CELL_COUNT + 1)]
    valid_values = [v for v in values if v is not None]
    spread = (max(valid_values) - min(valid_values)) * 1000 if len(valid_values) >= 2 else None
    stack = stack_value(row)
    motor = current_value(row, 1)
    packet = f"#{row.get('packet_number')}" if row else "—"
    note = "Latest received" if state == "Live" else "Last received · " + state.lower()
    st.markdown('<div class="summary-grid">' +
                summary_card("Stack voltage", fmt(stack, 2), "V", note, True) +
                summary_card("Motor current · S1", fmt(motor), "A", "Signed current" if motor is not None else "Awaiting valid measurement") +
                summary_card("Cell spread", fmt(spread, 0), "mV", f"Across {len(valid_values)} valid cell channels") +
                summary_card("Latest packet", packet, "", "Session " + str(row.get("session_id", ""))[:8] if row else "Waiting for ESP A") + '</div>', unsafe_allow_html=True)

    if not CONFIGURED:
        message, banner_tone = "Add your Supabase server key in Streamlit Secrets to connect this dashboard.", "warn"
    elif error:
        message, banner_tone = error, "bad"
    elif active_alerts:
        message, banner_tone = f"CURRENT ALERT — Sensor {', '.join(map(str, active_alerts))} exceeds {threshold:.2f} A for at least {hold_seconds} s.", "bad"
    elif state != "Live":
        message, banner_tone = {"Paused": "Refresh paused. Displayed values are the last received readings.", "No data": "Connected to the database. Waiting for records from esp-a.", "Offline": "No recent upload. Displayed readings are stale.", "Delayed data": "Uploads are arriving, but their measurement timestamps are old. Catching up.", "Time not synced": "Measurement time is unavailable. Waiting for ESP A clock synchronisation.", "Clock mismatch": "ESP A timestamp is ahead of the dashboard clock."}.get(state, state), "warn"
    elif not fresh_bms or len(valid_values) < CELL_COUNT:
        message, banner_tone = "BMS readings are stale or one or more cell channels are unavailable.", "warn"
    elif row and not row.get("current_zeroed"):
        message, banner_tone = "Current sensor needs zero calibration. Motor off, then send z to ESP A.", "warn"
    elif row and row.get("current_range_error"):
        message, banner_tone = "Current sensor ADC is outside its accepted range. Check sensor power and wiring.", "bad"
    else:
        message, banner_tone = "Receiving telemetry · dashboard refreshes once per second", ""
    st.markdown(f'<div class="banner {banner_tone}"><span class="pill {tone}"><span class="dot {tone}"></span>{escape(state)}</span>{escape(message)}</div>', unsafe_allow_html=True)

    left, right = st.columns([1, 1], gap="medium")
    with left:
        st.markdown(f'<div class="section-head"><span class="section-title">Battery cells</span><span class="section-meta">{CELL_COUNT} configured / 6 slots</span></div>', unsafe_allow_html=True)
        tiles = []
        for i in range(1, 7):
            inactive = i > CELL_COUNT
            value = None if inactive else cell_value(row, i)
            label = "Not connected" if inactive else ("No reading" if value is None else ("Live voltage" if fresh_bms and state == "Live" else "Last received"))
            dot = "muted" if inactive or not row else ("good" if value is not None and fresh_bms and state == "Live" else "warn")
            tiles.append(channel_tile(f"Cell {i:02d}", value, "V", dot, label, inactive, voltage=True))
        st.markdown('<div class="cell-grid">' + ''.join(tiles) + '</div>', unsafe_allow_html=True)
    with right:
        st.markdown(f'<div class="section-head"><span class="section-title">Motor channels</span><span class="section-meta">{len(ACTIVE_CURRENTS)} configured / 8 slots</span></div>', unsafe_allow_html=True)
        tiles = []
        for i in range(1, 9):
            inactive = i not in ACTIVE_CURRENTS
            value = None if inactive else current_value(row, i)
            alarm = i in active_alerts
            if inactive: label = "Not connected"
            elif not row: label = "No reading"
            elif i == 1 and not row.get("current_zeroed"): label = "Needs zero"
            elif i == 1 and row.get("current_range_error"): label = "ADC range error"
            elif alarm: label = "Over threshold"
            elif value is None: label = "No reading"
            elif state != "Live": label = "Last received"
            else: label = "Live current"
            dot = "bad" if alarm else ("muted" if inactive or not row else ("good" if value is not None and state == "Live" else "warn"))
            tiles.append(channel_tile(f"Sensor {i:02d}", value, "A", dot, label, inactive, alarm))
        st.markdown('<div class="motor-grid">' + ''.join(tiles) + '</div>', unsafe_allow_html=True)
    st.write("")
    c1, c2 = st.columns(2, gap="medium")
    with c1:
        st.plotly_chart(chart(visible, "cells" if voltage_plot == "Cell voltages" else "stack"), use_container_width=True, config={"displayModeBar": False}, key="voltage_chart")
    with c2:
        st.plotly_chart(chart(visible, "current"), use_container_width=True, config={"displayModeBar": False}, key="current_chart")
    crc = row.get("bms_crc_error_count", 0) if row else "—"
    dropped = row.get("buffer_drop_count", 0) if row else "—"
    bms_age = f"{row.get('bms_age_ms')} ms" if row and row.get("bms_age_ms") is not None else "—"
    last = time_label(row.get("received_at")) if row else "—"
    st.markdown(f'<div class="health"><span>Last arrival <strong>{last}</strong></span><span>BMS age at upload <strong>{bms_age}</strong></span><span>Gaps in view <strong>{gaps}</strong></span><span>BMS CRC rejects <strong>{crc}</strong></span><span>Buffer drops <strong>{dropped}</strong></span></div>', unsafe_allow_html=True)
    st.caption("Gaps count missing packet numbers within the loaded window, separately for each restart session. Alerts operate while this page is open.")


def history_page():
    heading("Telemetry history", "Inspect saved packets and export a selected arrival-time window.")
    col1, col2 = st.columns([2, 1])
    with col1:
        hours = st.selectbox("Look back", [.25, 1, 6, 24], format_func=lambda h: "Last 15 minutes" if h == .25 else f"Last {h:g} hours")
    with col2:
        st.write("")
        load = st.button("Load history", type="primary", disabled=not CONFIGURED, use_container_width=True)
    if load:
        end = datetime.now(timezone.utc)
        try:
            with st.spinner("Reading saved telemetry…"):
                records, capped = history_rows(URL, KEY, DEVICE, end - timedelta(hours=hours), end)
            st.session_state["history_result"] = (records, capped, end.isoformat(), hours)
        except DataError as error:
            st.error(str(error))
    result = st.session_state.get("history_result")
    if not result:
        st.info("Choose a window and load history." if CONFIGURED else "Configure the Supabase connection first.")
        return
    records, capped, loaded_at, loaded_hours = result
    if capped:
        st.warning("Export capped at 10,000 rows. This is only part of the selected window; choose a shorter window for a complete export.")
    if not records:
        st.info("No records in this window.")
        return
    frame = pd.DataFrame(records)
    st.caption(f"{len(frame):,} records · {packet_gaps(records):,} internal packet gaps · loaded at {time_label(loaded_at)} · {loaded_hours:g} hour window")
    st.download_button("Download CSV", frame.to_csv(index=False).encode("utf-8"),
                       file_name=f"{DEVICE}_telemetry_{datetime.now(timezone.utc):%Y%m%d_%H%M%S}.csv", mime="text/csv")
    st.dataframe(frame.sort_values("received_at", ascending=False), use_container_width=True, height=440, hide_index=True)
    st.caption("CSV timestamps remain in UTC. Empty values are unavailable readings, not zero.")


def connection_page():
    heading("Connection", "Server-side access to your telemetry database.")
    st.markdown("**Project:** " + escape(URL))
    st.markdown("**Device:** `" + DEVICE.replace("`", "") + "`")
    st.markdown("**Credential:** " + ("Configured on server" if CONFIGURED else "Not configured"))
    if not CONFIGURED:
        st.info("In Streamlit Community Cloud, open App settings → Secrets. Add SUPABASE_URL and SUPABASE_KEY using secrets.example.toml. Use a Supabase server secret or legacy service_role key.")
    if st.button("Check database access", disabled=not CONFIGURED):
        try:
            records = poll_rows(URL, KEY, DEVICE)
            st.success(f"Read access works. Retrieved {len(records)} recent records for {DEVICE}.")
        except DataError as error:
            st.error(str(error))
    st.markdown("**Refresh:** once per second while Live overview is open. History loads on demand.")
    st.markdown("**Configured channels:** " + str(CELL_COUNT) + " cells; current sensors " + ", ".join(map(str, ACTIVE_CURRENTS)))
    st.caption("No database writes or motor-control commands are sent by this application. Keep the app private if its telemetry should not be publicly visible.")

if page == "Live overview":
    heading("Power overview", "Battery and motor telemetry, in one place.")
    overview()
elif page == "History & export":
    history_page()
else:
    connection_page()
