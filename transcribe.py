#!/usr/bin/env python3
"""Send an audio file to ChatGPT's internal transcribe endpoint and print the text to stdout.

Reuses the Codex login session (~/.codex/auth.json, see codex_auth.py), so there is no token to copy from a
browser; when the token is about to expire or gets rejected, Codex is asked to refresh it. Requests go through
Homebrew curl (OpenSSL): chatgpt.com's Cloudflare blocks the LibreSSL TLS fingerprint of /usr/bin/python3 and
/usr/bin/curl.

    python3 transcribe.py audio.webm [--duration-ms 4200] [--debug]

Exit code 0 = success (text on stdout). Non-zero = failure (message on stderr).
"""
import argparse
import json
import os
import shutil
import subprocess
import tempfile
import sys
import time
import unicodedata
import uuid

import codex_auth

ENDPOINT = "https://chatgpt.com/backend-api/transcribe"
# Homebrew lives in /opt/homebrew on Apple Silicon and /usr/local on Intel. Hammerspoon launched from Finder or
# Login Items only gets launchd's minimal PATH, so use absolute paths instead of searching PATH.
HOMEBREW = "/opt/homebrew" if os.path.isdir("/opt/homebrew") else "/usr/local"
CURL = f"{HOMEBREW}/opt/curl/bin/curl"
FFPROBE = f"{HOMEBREW}/bin/ffprobe"
# Mimic the endpoint's original client (the Codex app). A/B tested against the Codex CLI headers: same results.
USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/154.0.0.0 Safari/537.36"
)
CLIENT_HEADERS = [
    "originator: Codex Browser",
    "x-openai-web-frontend: codex_webview",
    "x-openai-codex-window-type: browser",
    "oai-language: en-US",
    "Origin: https://chatgpt.com",
    "Referer: https://chatgpt.com/",
]
TIMEOUT_SEC = 60
MAX_ATTEMPTS = 3  # the endpoint takes no language hint, so resend when it guesses the wrong language
MISDETECTED_COPY = os.path.join(tempfile.gettempdir(), "chatgpt-dictation-misdetected.webm")


class DictationError(Exception):
    pass


class TokenRejected(DictationError):
    pass


def load_auth(force_refresh=False):
    try:
        if force_refresh or codex_auth.hours_left() < 1:
            codex_auth.refresh_via_codex()
        return codex_auth.read_tokens()
    except codex_auth.AuthError as e:
        raise DictationError(str(e))


def probe_duration_ms(path):
    try:
        out = subprocess.run(
            [FFPROBE, "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", path],
            capture_output=True, text=True, timeout=10,
        ).stdout.strip()
        return float(out) * 1000
    except (OSError, ValueError, subprocess.TimeoutExpired):
        return 0.0


def post_audio(path, duration_ms, access_token, account_id, session_id):
    """Return (http_status, body). The token goes in through stdin (-K -) so it never shows up in `ps`."""
    if not os.path.exists(CURL):
        raise DictationError("Homebrew curl not found. Run `brew install curl`.")

    secret_config = (
        f'header = "Authorization: Bearer {access_token}"\n'
        f'header = "chatgpt-account-id: {account_id}"\n'
    )
    cmd = [
        CURL, "-sS", "-K", "-", "--max-time", str(TIMEOUT_SEC),
        "-X", "POST", ENDPOINT,
        "-A", USER_AGENT,
        *[arg for header in CLIENT_HEADERS for arg in ("-H", header)],
        "-F", f'file=@"{path}";type=audio/webm;filename=codex.webm',
        "-F", f"dictation_session_id={session_id}",
        "-F", f"attempt_id={uuid.uuid4()}",
        "-F", f"duration_ms={duration_ms:.0f}",
        "-w", "\n%{http_code}",
    ]
    proc = subprocess.run(cmd, input=secret_config, capture_output=True, text=True)
    if proc.returncode != 0:
        raise DictationError(f"curl failed ({proc.returncode}): {proc.stderr.strip()}")
    body, _, status = proc.stdout.rpartition("\n")
    return int(status), body


def is_vietnamese_or_english(text):
    """Accept Latin letters only (Vietnamese diacritics included). Thai, Chinese, Korean... mean a wrong guess."""
    return all(
        not ch.isalpha() or unicodedata.name(ch, "").startswith("LATIN")
        for ch in text
    )


def request_text(path, duration_ms, access_token, account_id, session_id, debug):
    started = time.time()
    status, raw = post_audio(path, duration_ms, access_token, account_id, session_id)

    if debug:
        print(f"[debug] HTTP {status} after {time.time() - started:.2f}s", file=sys.stderr)
        print(f"[debug] body: {raw[:500]}", file=sys.stderr)

    if status in (401, 403):
        if "<html" in raw.lower():
            raise DictationError(f"HTTP {status}: blocked by Cloudflare.")
        raise TokenRejected(f"HTTP {status}: token rejected.")
    if status == 429:
        raise DictationError("HTTP 429: rate limited, try again later.")
    if status >= 400:
        raise DictationError(f"HTTP {status}: {raw[:300]}")

    try:
        text = json.loads(raw).get("text", "")
    except (json.JSONDecodeError, AttributeError):
        raise DictationError(f"Unexpected non-JSON response: {raw[:300]}")
    if not text.strip():
        raise DictationError("Server returned no text (the audio may be silent).")
    return text.strip()


def transcribe(path, duration_ms=None, debug=False):
    if not os.path.isfile(path) or os.path.getsize(path) == 0:
        raise DictationError(f"Audio file is empty or missing: {path}")

    access_token, account_id = load_auth()
    duration_ms = duration_ms or probe_duration_ms(path)
    session_id = uuid.uuid4()  # retries are separate attempts of the same session
    for attempt in range(1, MAX_ATTEMPTS + 1):
        try:
            text = request_text(path, duration_ms, access_token, account_id, session_id, debug)
        except TokenRejected:
            if attempt > 1:
                raise
            access_token, account_id = load_auth(force_refresh=True)  # token revoked early: refresh and retry
            text = request_text(path, duration_ms, access_token, account_id, session_id, debug)
        if is_vietnamese_or_english(text):
            return text
        print(f"[attempt {attempt}] wrong language detected: {text[:60]}", file=sys.stderr)

    shutil.copyfile(path, MISDETECTED_COPY)  # keep it around for debugging
    raise DictationError(f"Wrong language detected {MAX_ATTEMPTS} times in a row. Try again with a slightly longer sentence.")


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("audio")
    parser.add_argument("--duration-ms", type=float)
    parser.add_argument("--debug", action="store_true", help="Print the HTTP status and raw response to stderr")
    args = parser.parse_args()
    try:
        print(transcribe(args.audio, args.duration_ms, args.debug))
    except DictationError as e:
        print(str(e), file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
