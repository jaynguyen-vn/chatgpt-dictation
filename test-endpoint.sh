#!/usr/bin/env bash
# Test the transcribe endpoint without a mic: synthesize Vietnamese speech with `say`,
# convert it to webm/opus the way ChatGPT sends it, then run transcribe.py in debug mode.
#   ./test-endpoint.sh [sentence to speak]
set -euo pipefail

DIR="$(cd "$(dirname "$0")" && pwd)"
WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT

TEXT="${1:-Xin chào, đây là bài kiểm tra tính năng chuyển giọng nói thành văn bản qua tài khoản ChatGPT.}"

if ! say -v '?' | grep -q '^Linh '; then
  echo "The Vietnamese voice 'Linh' is not installed. Add it in System Settings → Accessibility → Spoken Content." >&2
  exit 1
fi

say -v Linh -o "$WORK/sample.aiff" "$TEXT"
ffmpeg -hide_banner -loglevel error -y -i "$WORK/sample.aiff" \
  -ac 1 -ar 48000 -c:a libopus -b:a 32k "$WORK/sample.webm"

echo "Original: $TEXT"
printf "Result  : "
/usr/bin/python3 "$DIR/transcribe.py" "$WORK/sample.webm" --debug
