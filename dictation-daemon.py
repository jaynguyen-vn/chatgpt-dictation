#!/usr/bin/env python3
"""Recording daemon for dictation: keeps the mic open and always buffers the last few hundred ms of audio,
so the first word isn't lost even if you start talking the moment you press the key.

Hammerspoon drives it over stdin, one command per line: start | stop | cancel.
The daemon reports events on stdout, one JSON object per line:
    {"event": "ready"}                       mic is open and buffering
    {"event": "level", "db": -32.5}          level (dBFS) of each 50 ms chunk, only sent while recording
    {"event": "text", "text": "..."}         result of a stop command
    {"event": "error", "message": "..."}     failure of a stop (every stop yields exactly one text or one error)
    {"event": "mic_lost", "message": "..."}  the mic stream died and the daemon is about to exit
Audio only lives in RAM (a ring buffer); it is encoded and sent only after a stop command.
Closing stdin (Hammerspoon quit/reload) makes the daemon stop ffmpeg and exit.

    python3 dictation-daemon.py [--preroll 0.5] [--mic :default]
"""
import argparse
import array
import collections
import json
import math
import os
import subprocess
import sys
import tempfile
import threading

import transcribe

FFMPEG = f"{transcribe.HOMEBREW}/bin/ffmpeg"
SAMPLE_RATE = 48000
BYTES_PER_SEC = SAMPLE_RATE * 2  # s16le mono
CHUNK_BYTES = BYTES_PER_SEC // 20  # 50 ms
# A/B tested against the endpoint: loudness normalization helps quiet speech; denoising (afftdn) made results
# worse, so it is not used.
AUDIO_FILTERS = "highpass=f=80,dynaudnorm=f=150:g=15:p=0.9:m=10"
BITRATE = "64k"
SILENCE_DB = -100.0

_emit_lock = threading.Lock()


def emit(event, **data):
    with _emit_lock:
        print(json.dumps({"event": event, **data}, ensure_ascii=False), flush=True)


def level_db(chunk):
    """RMS level of an s16le chunk in dBFS: 0 is the loudest, more negative is quieter."""
    samples = array.array("h", chunk[: len(chunk) // 2 * 2])  # "h" uses native byte order; every Mac is little-endian
    rms = math.sqrt(sum(s * s for s in samples) / len(samples)) if samples else 0
    return 20 * math.log10(rms / 32768) if rms else SILENCE_DB


class Recorder:
    def __init__(self, mic, preroll_sec):
        self.lock = threading.Lock()
        self.preroll = collections.deque()
        self.preroll_bytes = 0
        self.preroll_max = int(preroll_sec * BYTES_PER_SEC)
        self.take = None  # list of chunks while recording, None while idle
        self.closing = False
        self.capture = subprocess.Popen(
            [FFMPEG, "-hide_banner", "-loglevel", "error", "-f", "avfoundation", "-i", mic,
             "-ac", "1", "-ar", str(SAMPLE_RATE), "-f", "s16le", "-"],
            stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
        )
        threading.Thread(target=self._read_mic, daemon=True).start()

    def _read_mic(self):
        announced = False
        while True:
            chunk = self.capture.stdout.read(CHUNK_BYTES)
            if not chunk:
                break
            if not announced:
                emit("ready")
                announced = True
            with self.lock:
                recording = self.take is not None
                if recording:
                    self.take.append(chunk)
                else:
                    self.preroll.append(chunk)
                    self.preroll_bytes += len(chunk)
                    while self.preroll_bytes > self.preroll_max and self.preroll:
                        self.preroll_bytes -= len(self.preroll.popleft())
            if recording:
                emit("level", db=round(level_db(chunk), 1))
        if self.closing:
            return
        emit("mic_lost", message="Mic stream stopped (ffmpeg exited). Check the Microphone permission or the input device.")
        os._exit(1)

    def start(self):
        with self.lock:
            if self.take is None:
                self.take = list(self.preroll)
                self.preroll.clear()
                self.preroll_bytes = 0

    def stop(self):
        with self.lock:
            chunks, self.take = self.take, None
        return b"".join(chunks or [])

    def cancel(self):
        self.stop()

    def close(self):
        self.closing = True
        self.capture.terminate()


def encode_webm(pcm, path):
    subprocess.run(
        [FFMPEG, "-hide_banner", "-loglevel", "error", "-y",
         "-f", "s16le", "-ar", str(SAMPLE_RATE), "-ac", "1", "-i", "-",
         "-af", AUDIO_FILTERS, "-c:a", "libopus", "-b:a", BITRATE, path],
        input=pcm, check=True,
    )


def transcribe_take(pcm):
    fd, path = tempfile.mkstemp(prefix="chatgpt-dictation-", suffix=".webm")
    os.close(fd)
    try:
        encode_webm(pcm, path)
        emit("text", text=transcribe.transcribe(path, len(pcm) / BYTES_PER_SEC * 1000))
    except transcribe.DictationError as e:
        emit("error", message=str(e))
    except subprocess.CalledProcessError:
        emit("error", message="ffmpeg failed to encode the audio.")
    finally:
        os.remove(path)


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--preroll", type=float, default=0.5, help="Seconds of audio kept from before the key press")
    parser.add_argument("--mic", default=":default", help='avfoundation input device, e.g. ":default"')
    args = parser.parse_args()

    recorder = Recorder(args.mic, args.preroll)
    try:
        for line in sys.stdin:
            command = line.strip()
            if command == "start":
                recorder.start()
            elif command == "stop":
                pcm = recorder.stop()
                if pcm:
                    threading.Thread(target=transcribe_take, args=(pcm,), daemon=True).start()
                else:
                    emit("error", message="No audio captured (the mic wasn't open yet).")
            elif command == "cancel":
                recorder.cancel()
    finally:
        recorder.close()


if __name__ == "__main__":
    main()
