# ChatGPT Dictation

Push-to-talk dictation for macOS that works in any app: hold **Right Option**, speak, release, and the text is
pasted at the cursor. Speech-to-text goes through ChatGPT's internal transcribe endpoint, signed in with the
Codex login already on your Mac, so you don't need a separate API key. It recognizes Vietnamese and English.

> This is a personal tool and is not affiliated with OpenAI. It relies on an internal API that can break at any
> time, and you use it at your own risk to your ChatGPT account. See [Limitations](#limitations).

## Requirements

- macOS on Apple Silicon or Intel.
- [Homebrew](https://brew.sh) and the Command Line Tools (`xcode-select --install`). Hammerspoon runs the
  system `/usr/bin/python3`; no Python packages are needed.
- Codex (Codex.app or the `codex` CLI), signed in with `codex login` using a ChatGPT account, not an API key.

`install.sh` installs the rest: ffmpeg, Homebrew curl (required; the `transcribe.py` docstring explains why)
and [Hammerspoon](https://www.hammerspoon.org/).

## Install

```sh
git clone https://github.com/jaynguyen-vn/chatgpt-dictation.git
cd chatgpt-dictation
./install.sh
```

The script is safe to run again. It adds a `dofile(".../dictation.lua")` line to `~/.hammerspoon/init.lua`,
then prints the remaining steps: reload the Hammerspoon config, and grant **Accessibility** (to catch the key
and paste) and **Microphone** access. If you move the repo, run `./install.sh` again and delete the old
`dofile` line.

To use the `hs` command in a terminal (see [Testing](#testing)), add `require("hs.ipc")` to `init.lua`.

## Usage

- Hold Right Option, wait until the HUD at the bottom of the screen shows a red dot and "Listening…", speak,
  then release. The text is pasted with Cmd+V, and your previous clipboard is restored right after.
- Every state appears in the same HUD at the bottom of the screen, told apart by the dot color: grey while the
  mic opens, red while listening, blue for "Transcribing…", green for "Pasted", orange for errors.
- The bars in the HUD follow the mic level. If they rise while you talk, the mic hears you. If they stay almost
  flat, the mic isn't picking up sound: check `MIC` or the mic's input volume.
- While the key is held, the speakers or headphones are muted so other sounds don't leak into the mic, and
  they are unmuted when you release. If the output was already muted, it stays muted. The short buffer from
  before the key press can still contain other sounds, because nothing is muted yet at that point. Devices that
  can't be muted (often display speakers over HDMI) keep playing.
- On the first press while the mic is closed, the HUD shows a grey dot and "Opening mic…" for about half a
  second. From the second take on, the mic is already open and keeps a short buffer from before the press, so
  the first word isn't lost even if you start talking right away.
- Pressing another key while holding Option cancels the take, so Option+key shortcuts keep working. Very short
  presses are ignored as accidental. If you forget to release the key, recording stops on its own.
- The orange dot in the menu bar means the mic is open. The mic closes after a while without use, when the
  screen locks, when the Mac sleeps, or when the default mic changes. With a Bluetooth headset the mic closes
  right after each take, because keeping a Bluetooth mic open switches the headset to call mode and music
  sounds bad.

## Configuration

The constants live at the top of `dictation.lua`. Reload the Hammerspoon config after editing them.

| Constant | Meaning |
|---|---|
| `MIC` | Input device. List devices with `ffmpeg -f avfoundation -list_devices true -i ""` |
| `KEEP_WARM_SEC` | How long the mic stays open after the last take |
| `PREROLL_SEC` | How much audio from before the key press is kept |
| `MIN_MS` | Presses shorter than this are ignored |
| `MAX_SEC` | Recording stops on its own after this many seconds |
| `REFRESH_UNDER_HOURS` | Ask Codex to refresh the token once fewer hours than this are left |
| `RIGHT_OPTION` | The push-to-talk key |
| `LEVEL_FLOOR_DB`, `LEVEL_CEIL_DB` | Scale of the level meter: below the floor the bars are flat, at the ceiling they are full. Raise the floor if the bars stay high in a quiet room |

## Testing

| Command | Use it to |
|---|---|
| `./test-endpoint.sh ["sentence to speak"]` | Test the whole path to the endpoint without a mic. It sends a real request with your account and needs the macOS Vietnamese voice "Linh" |
| `python3 transcribe.py file.webm --debug` | Send any audio file and see the raw response |
| `python3 codex_auth.py` | See how many hours the token has left (add `--force` to refresh now) |
| `hs -c 'chatgptDictation.status()'` | See the mic and recording state (needs `hs.ipc`) |

Logs are in the Hammerspoon Console, on lines starting with `[dictation]`.

## Troubleshooting

| Symptom | What to do |
|---|---|
| Nothing happens when you hold Right Option | Check that Hammerspoon is running, has Accessibility access, and that `init.lua` has the `dofile` line. Look at the Console |
| "Mic stream stopped" | Grant Hammerspoon Microphone access in System Settings → Privacy & Security → Microphone, then check `MIC` |
| The bars stay flat while you talk | The mic isn't picking up sound. Check `MIC` and the input volume in System Settings → Sound |
| "No audio captured" | On the first press the mic wasn't open yet. Wait for "Listening…" before talking |
| "Server returned no text" | The take had no speech in it, or the speech was too quiet |
| "auth.json not found" or "token was not extended" | Run `codex login` again |
| "couldn't refresh the Codex token" | Run `python3 codex_auth.py --force` to see the detailed error |
| "Homebrew curl not found" | `brew install curl` |
| "blocked by Cloudflare" | Cloudflare or the Codex client may have changed. See `USER_AGENT` and `CLIENT_HEADERS` in `transcribe.py` |
| "HTTP 429" | You are being rate limited. Wait a while and try again |
| "Wrong language detected" | Say it again, a bit longer. The audio of the failed take is kept at `$TMPDIR/chatgpt-dictation-misdetected.webm` |

## Limitations

- The endpoint is an undocumented internal API, and the code imitates the Codex app's headers. If OpenAI
  changes the headers, the authentication, or the Cloudflare rules, the tool can break at any time.
- The endpoint takes no language hint, so it sometimes guesses Thai, Chinese, Korean and so on. The code only
  accepts Latin script and resends when it gets anything else. Short sentences are misdetected more often.
- Audio only leaves your Mac when you release the key, but once sent it is stored on the server: the response
  includes an `asset_pointer` with an `asset_ttl`, which was 30 days when last checked.

## Files

| File | Role |
|---|---|
| `dictation.lua` | Hammerspoon script: catches the key, draws the HUD, pastes the text, opens and closes the mic, refreshes the token on a schedule |
| `dictation-daemon.py` | Holds the mic through ffmpeg, buffers audio in RAM, and encodes and sends it on a `stop` command |
| `transcribe.py` | Calls the transcribe endpoint and resends on a wrong-language guess. Also works on its own as a CLI |
| `codex_auth.py` | Reads the Codex token and asks `codex app-server` to refresh it |
| `test-endpoint.sh` | Synthesizes speech with `say` and runs `transcribe.py` on it |
| `install.sh` | Installs the dependencies through Homebrew and loads `dictation.lua` into Hammerspoon |

## License

[MIT](LICENSE)
