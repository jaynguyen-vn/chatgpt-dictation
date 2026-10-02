# CLAUDE.md

What the project does and how to install it: see `README.md`.

## Runtime

- Hammerspoon runs Python as macOS's `/usr/bin/python3` (3.9), with no venv and no pip. Use the standard
  library only, and syntax that runs on 3.9.
- Hammerspoon runs Lua 5.4, and the code uses the `&` operator. Don't syntax-check with `luajit`: it is Lua 5.1
  and reports false errors.
- Every request to chatgpt.com must go through `CURL` (Homebrew curl) in `transcribe.py`. Don't switch to
  `urllib`, `requests` or `/usr/bin/curl`: Cloudflare blocks the LibreSSL TLS fingerprint.
- Hammerspoon launched from Finder or Login Items only has launchd's minimal PATH. Reference external tools by
  absolute path (see `HOMEBREW` in `transcribe.py`), never by bare command name.

## Don'ts

- Don't call the OAuth refresh API yourself and don't write `~/.codex/auth.json`. Refresh only through
  `codex_auth.refresh_via_codex()`. The refresh token rotates on every use, so a bad write loses the Codex
  login.
- Don't print or log tokens, and never put them in argv. Don't read `~/.codex/auth.json` into the conversation.
  The token reaches curl through stdin (`-K -`).
- Don't write raw audio to disk. The only exceptions today are the temporary webm (deleted right after it is
  sent) and the copy kept when the language is misdetected.
- Don't edit `~/.hammerspoon/init.lua` without asking. To test `install.sh`, run it with `HOME` pointed at a
  temporary directory.
- `test-endpoint.sh` and `codex_auth.py --force` use the real account. Run them only when a change touches
  transcription or auth.
- When testing with `chatgptDictation.start()`, Right Option isn't really held, so any modifier key the user
  presses ends the take and sends the room audio to the server. Call `chatgptDictation.tap:stop()` first, end
  with `chatgptDictation.stop(true)` (cancel, nothing is sent), then call `chatgptDictation.tap:start()`.

## Cross-file contracts

- `dictation.lua` and `dictation-daemon.py` talk over stdin/stdout using the protocol in the daemon's
  docstring. Change one side and you must change the other. Every `stop` command must produce exactly one
  `text` or `error` event, because `dictation.lua` relies on the `inFlight` counter to decide when to close the
  mic.
- In Hammerspoon, every new timer, task, watcher and eventtap must be kept referenced (a module-level variable
  or a field of `M`). Otherwise it is garbage-collected and silently stops running.

## Before reporting done

There are no automated tests.

- Python: `/usr/bin/python3 -m py_compile codex_auth.py dictation-daemon.py transcribe.py`
- Lua syntax: `hs -q -c 'local f, e = loadfile("<absolute path>/dictation.lua"); return f and "syntax OK" or e' </dev/null`
- After changing Lua: reload with `hs -q -c '_G.__r = hs.timer.doAfter(0.3, hs.reload)' </dev/null`, then run
  `hs -q -c 'chatgptDictation.status()' </dev/null` to confirm the module reloaded.
- Always add `</dev/null` when an agent calls `hs`. If stdin is a pipe, `hs` waits to read it and hangs.
- After changing `transcribe.py` or `codex_auth.py`: run `./test-endpoint.sh`.

## Conventions

Everything is written in English: code comments, docstrings, docs, and all user-facing text (HUD, error
messages, CLI output). The one exception is the Vietnamese sample sentence in `test-endpoint.sh`, which is test
data for Vietnamese recognition.
