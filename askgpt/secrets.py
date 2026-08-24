"""Secret detection for outbound payloads.

Findings halt the run. Excerpts are redacted: printing a secret in order to
warn about the secret defeats the purpose.
"""

import re
from collections import namedtuple

Finding = namedtuple("Finding", "name line excerpt")

PATTERNS = (
    ("openai-key", re.compile(r"\bsk-[A-Za-z0-9_-]{16,}")),
    ("github-pat", re.compile(r"\bgh[pousr]_[A-Za-z0-9]{20,}")),
    ("aws-access-key", re.compile(r"\bAKIA[0-9A-Z]{16}\b")),
    ("bearer-token", re.compile(r"\bBearer\s+[A-Za-z0-9._~+/-]{20,}")),
    ("private-key-block", re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----")),
    ("slack-token", re.compile(r"\bxox[baprs]-[A-Za-z0-9-]{10,}")),
    ("google-api-key", re.compile(r"\bAIza[0-9A-Za-z_-]{35,}")),
    ("stripe-key", re.compile(r"\b[sr]k_(?:live|test)_[0-9A-Za-z]{16,}")),
    ("npm-token", re.compile(r"\bnpm_[A-Za-z0-9]{30,}")),
    ("pypi-token", re.compile(r"\bpypi-[A-Za-z0-9_-]{16,}")),
    ("jwt", re.compile(r"\beyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}")),
    # The catch-all for formats the specific patterns do not know. Keyed on the
    # NAME rather than the value, because an unfamiliar credential format is
    # unrecognisable by shape -- which is exactly the gap the specific patterns
    # leave. Quoted, 12+ chars, no whitespace.
    (
        "assigned-credential",
        # The leading prefix is BOUNDED and lazy. An unbounded `[A-Za-z0-9_]*`
        # here backtracks quadratically: on a long alphanumeric run with no
        # keyword it retries the alternation at every position, O(n^2) over the
        # file. That hung the content preflight on ordinary minified/blob files,
        # not just crafted ones. A 40-char prefix covers real variable names
        # (DATABASE_PASSWORD) while keeping the scan linear.
        re.compile(
            r"(?i)(?<![A-Za-z0-9_])[A-Za-z0-9_]{0,40}?"
            r"(?:pass(?:word|wd)?|secret|token|api[_-]?key|access[_-]?key"
            r"|auth[_-]?key|private[_-]?key|credential)s?\s*[:=]\s*"
            r"[\"'][^\"'\s]{12,}[\"']"
        ),
    ),
)


def _redact(match_text):
    """Six leading chars plus a length. Deliberate: the prefix is already known
    from the pattern name and the length discloses no meaningful entropy, while
    both together let a user recognise WHICH credential was matched without the
    secret being reprinted into their terminal and scrollback."""
    head = match_text[:6]
    return head + "..." + str(len(match_text)) + " chars"


def scan(text):
    """Return a list of Finding for every secret-shaped match in `text`."""
    findings = []
    for line_number, line in enumerate(text.splitlines(), start=1):
        for name, pattern in PATTERNS:
            for match in pattern.finditer(line):
                findings.append(
                    Finding(name=name, line=line_number, excerpt=_redact(match.group(0)))
                )
    return findings


def format_findings(findings, payload_path=None):
    """Human-readable summary for the halt message.

    Takes the payload path because telling someone to "inspect the payload"
    without saying where it is makes the advice impossible to follow.
    """
    lines = ["Possible secrets detected in the payload:"]
    for finding in findings:
        lines.append("  line " + str(finding.line) + ": " + finding.name + " (" + finding.excerpt + ")")
    lines.append("")
    lines.append("Nothing was sent.")
    if payload_path:
        lines.append("The payload is at " + str(payload_path) + " -- inspect that line,")
        lines.append("then re-run with --allow-secrets if these are false positives.")
    else:
        lines.append("Re-run with --allow-secrets if these are false positives.")
    return "\n".join(lines)
