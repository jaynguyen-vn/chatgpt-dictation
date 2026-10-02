#!/usr/bin/env python3
"""Read Codex's ChatGPT login token (~/.codex/auth.json) and keep it fresh.

Never calls the refresh API directly: the refresh token rotates on every use, and writing the file wrong
breaks the Codex login. Codex does it instead, through `codex app-server` (method `account/read` with
`refreshToken: true`), and rewrites auth.json itself.

    python3 codex_auth.py                     # print how many hours the token has left
    python3 codex_auth.py --refresh-under 72  # refresh if fewer than 72 hours are left
    python3 codex_auth.py --force             # refresh now
"""
import argparse
import base64
import json
import os
import queue
import shutil
import subprocess
import sys
import threading
import time

AUTH_PATH = os.path.expanduser("~/.codex/auth.json")
# The native binary bundled with Codex.app is more stable than the npm one (whose path changes with the node
# version manager). The Homebrew build has a fixed path; `which` is the last resort because Hammerspoon launched
# from Login Items has a minimal PATH.
CODEX_CANDIDATES = [
    "/Applications/Codex.app/Contents/Resources/codex",
    "/opt/homebrew/bin/codex",
    "/usr/local/bin/codex",
    shutil.which("codex"),
]
REFRESH_TIMEOUT_SEC = 30


class AuthError(Exception):
    pass


def jwt_claims(token):
    payload = token.split(".")[1]
    payload += "=" * (-len(payload) % 4)
    return json.loads(base64.urlsafe_b64decode(payload))


def read_tokens():
    """Return (access_token, account_id)."""
    try:
        with open(AUTH_PATH) as f:
            tokens = json.load(f).get("tokens") or {}
    except FileNotFoundError:
        raise AuthError("~/.codex/auth.json not found. Run `codex login` first.")
    if not tokens.get("access_token") or not tokens.get("account_id"):
        raise AuthError("auth.json is missing access_token/account_id. Run `codex login` again.")
    return tokens["access_token"], tokens["account_id"]


def hours_left():
    access_token, _ = read_tokens()
    return (jwt_claims(access_token).get("exp", 0) - time.time()) / 3600


def find_codex():
    for path in CODEX_CANDIDATES:
        if path and os.access(path, os.X_OK):
            return path
    raise AuthError("Codex not found (neither Codex.app nor the `codex` command).")


def refresh_via_codex():
    """Ask Codex app-server to refresh the token. Return the hours left after the refresh."""
    before = hours_left()
    proc = subprocess.Popen(
        [find_codex(), "app-server"],
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True,
    )
    lines = queue.Queue()
    threading.Thread(target=lambda: [lines.put(line) for line in proc.stdout], daemon=True).start()

    def send(message):
        proc.stdin.write(json.dumps(message) + "\n")
        proc.stdin.flush()

    def wait_for(request_id, deadline):
        while True:
            try:
                line = lines.get(timeout=max(0.1, deadline - time.time()))
            except queue.Empty:
                raise AuthError("Codex app-server did not respond in time.")
            try:
                message = json.loads(line)
            except json.JSONDecodeError:
                continue
            if message.get("id") == request_id:
                if "error" in message:
                    raise AuthError(f"Codex refused to refresh: {message['error'].get('message', message['error'])}")
                return message.get("result")

    deadline = time.time() + REFRESH_TIMEOUT_SEC
    try:
        send({"id": 1, "method": "initialize",
              "params": {"clientInfo": {"name": "chatgpt-dictation", "title": "ChatGPT Dictation", "version": "0.1.0"}}})
        wait_for(1, deadline)
        send({"method": "initialized"})
        send({"id": 2, "method": "account/read", "params": {"refreshToken": True}})
        wait_for(2, deadline)
    finally:
        proc.stdin.close()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.terminate()

    after = hours_left()
    if after <= before:
        raise AuthError("Codex ran but the token was not extended. You may need to run `codex login` again.")
    return after


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--refresh-under", type=float, metavar="HOURS", help="Refresh if fewer than HOURS are left")
    group.add_argument("--force", action="store_true", help="Refresh now")
    args = parser.parse_args()
    try:
        left = hours_left()
        if args.force or (args.refresh_under is not None and left < args.refresh_under):
            left = refresh_via_codex()
            print(f"Token refreshed, {left:.1f} hours left.")
        else:
            print(f"Token valid for {left:.1f} more hours, no refresh needed.")
    except AuthError as e:
        print(str(e), file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
