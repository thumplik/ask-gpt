"""Report quota from Codex's own rate-limit records.

Codex writes a `rate_limits` block into its session rollout on every run, so
usage can be reported without spending any. Checking how much allowance is
left should not consume the allowance, and because each real run refreshes
the record, ordinary use keeps the figure current for free.
"""

import json
import os
import time
from pathlib import Path

# Newest-first, so the first file holding a record wins. Bounded because a
# long-lived install accumulates sessions and a full scan is wasted work.
MAX_FILES_SCANNED = 25


def sessions_dir(env=None):
    env = os.environ if env is None else env
    home = env.get("CODEX_HOME")
    if not home:
        home = str(Path(env.get("HOME") or Path.home()) / ".codex")
    return Path(home) / "sessions"


def read_latest(sessions_root):
    """Return (timestamp, rate_limits) from the most recent run, or None."""
    root = Path(sessions_root)
    if not root.is_dir():
        return None

    try:
        files = sorted(
            root.rglob("rollout-*.jsonl"),
            key=lambda p: p.stat().st_mtime,
            reverse=True,
        )
    except OSError:
        return None

    for path in files[:MAX_FILES_SCANNED]:
        found = None
        try:
            handle = path.open("r", encoding="utf-8", errors="replace")
        except OSError:
            continue
        with handle:
            for line in handle:
                if '"rate_limits"' not in line:
                    continue
                try:
                    event = json.loads(line)
                except ValueError:
                    continue
                limits = (event.get("payload") or {}).get("rate_limits")
                stamp = event.get("timestamp")
                if limits and limits.get("primary") and stamp:
                    found = (stamp, limits)  # last record in the file is newest
        if found:
            return found
    return None


def used_percent(record):
    return float((record[1].get("primary") or {}).get("used_percent") or 0.0)


def format_usage(record):
    """Multi-line report for `askgpt usage`."""
    if record is None:
        return (
            "No Codex usage data found yet.\n"
            "Codex records this on every run, so it appears after your first review."
        )

    stamp, limits = record
    primary = limits.get("primary") or {}
    window_days = (primary.get("window_minutes") or 0) / 1440.0

    lines = [
        "Plan:   " + str(limits.get("plan_type") or "unknown"),
        "Used:   {:.0f}% of the {:.0f}-day window".format(
            used_percent(record), window_days
        ),
    ]
    resets = primary.get("resets_at")
    if resets:
        lines.append(
            "Resets: " + time.strftime("%Y-%m-%d %H:%M", time.localtime(resets))
        )
    credits = limits.get("credits") or {}
    if credits.get("unlimited"):
        lines.append("Credits: unlimited")
    elif credits.get("has_credits"):
        lines.append("Credits: " + str(credits.get("balance")))

    lines.append("")
    lines.append("Read from Codex's own session log at " + stamp + ".")
    lines.append("It is only as fresh as your last Codex run; any review updates it.")
    return "\n".join(lines)


def format_footer(record):
    """One line printed after a run, or None when there is nothing to say."""
    if record is None:
        return None
    return "quota: {:.0f}% of the plan window used".format(used_percent(record))
