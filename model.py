"""Telemetry state calculations: timestamps, validity, gaps and current alerts."""
import math
from datetime import datetime, timezone

def timestamp(value):
    if not value:
        return None
    try:
        t = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        return t.replace(tzinfo=timezone.utc) if t.tzinfo is None else t.astimezone(timezone.utc)
    except (ValueError, TypeError):
        return None

def finite(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)

def identity(row):
    return row.get("device_id"), row.get("session_id"), row.get("packet_number")

def merge_rows(existing, new, limit=3600):
    merged = {identity(r): r for r in existing}
    merged.update({identity(r): r for r in new})
    return sorted(merged.values(), key=lambda r: (
        timestamp(r.get("received_at")) or datetime.min.replace(tzinfo=timezone.utc),
        str(r.get("session_id", "")), r.get("packet_number", 0)))[-limit:]

def latest_row(rows):
    if not rows:
        return None
    newest_arrival = max(rows, key=lambda r: timestamp(r.get("received_at")) or datetime.min.replace(tzinfo=timezone.utc))
    session = newest_arrival.get("session_id")
    return max((r for r in rows if r.get("session_id") == session), key=lambda r: r.get("packet_number", 0))

def data_age(row, now):
    if not row:
        return None
    t = timestamp(row.get("recorded_at"))
    # Unknown sampling time is deliberately NOT treated as fresh measurement.
    return max(0.0, (now - t).total_seconds()) if t else None

def link_state(row, now, query_error=False):
    if not row:
        return "No data", "muted"
    if query_error:
        return "Connection error", "bad"
    received = timestamp(row.get("received_at"))
    if not received or (now - received).total_seconds() > 5:
        return "Offline", "bad"
    measured = timestamp(row.get("recorded_at"))
    if measured is None:
        return "Time not synced", "warn"
    if (measured - now).total_seconds() > 5:
        return "Clock mismatch", "warn"
    if data_age(row, now) > 5:
        return "Delayed data", "warn"
    return "Live", "good"

def bms_fresh(row, now):
    age = data_age(row, now)
    return bool(row and not row.get("bms_stale", True) and age is not None and
                age + (row.get("bms_age_ms") or 0) / 1000 <= 3.5)

def cell_value(row, index):
    if not row:
        return None
    value = row.get(f"cell_{index}_v")
    # v2 mask has only four cell bits and one stack bit.
    mask_ok = index > 4 or bool((row.get("bms_valid_mask") or 0) & (1 << (index - 1)))
    return value if mask_ok and finite(value) and not row.get("bms_stale", True) else None

def stack_value(row):
    if not row or row.get("bms_stale", True) or not ((row.get("bms_valid_mask") or 0) & 16):
        return None
    return row.get("stack_voltage_v") if finite(row.get("stack_voltage_v")) else None

def current_value(row, index):
    if not row:
        return None
    if index == 1 and (not row.get("current_zeroed", False) or row.get("current_range_error", False)):
        return None
    value = row.get(f"current_{index}_a")
    return value if finite(value) else None

def packet_gaps(rows):
    """Only internal gaps in the loaded window, grouped by device and boot."""
    sessions = {}
    for row in rows:
        n = row.get("packet_number")
        if isinstance(n, int):
            sessions.setdefault((row.get("device_id"), row.get("session_id")), set()).add(n)
    return sum(max(nums) - min(nums) + 1 - len(nums) for nums in sessions.values() if nums)

def overcurrent_channels(rows, row, now, channels, threshold, hold_seconds, enabled):
    if not enabled or not row or link_state(row, now)[0] != "Live":
        return []
    history = sorted((r for r in rows if r.get("session_id") == row.get("session_id") and
                      r.get("packet_number", 0) <= row.get("packet_number", 0)),
                     key=lambda r: r.get("packet_number", 0), reverse=True)
    triggered = []
    for ch in channels:
        start_uptime = row.get("uptime_ms", 0)
        expected = row.get("packet_number")
        previous_uptime = start_uptime
        for r in history:
            value = current_value(r, ch)
            uptime = r.get("uptime_ms", 0)
            if (r.get("packet_number") != expected or value is None or abs(value) < threshold or
                    previous_uptime - uptime > 2500 or uptime > previous_uptime):
                break
            if (start_uptime - uptime) / 1000 >= hold_seconds:
                triggered.append(ch)
                break
            expected -= 1
            previous_uptime = uptime
    return triggered
