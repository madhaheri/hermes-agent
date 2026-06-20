#!/usr/bin/env bash
# Xiaoyuzhou podcast transcription script.
# Downloads audio, transcodes to 16kHz mono WAV, slices into <2h chunks,
# sends each chunk to Groq Whisper API, and concatenates the full transcript.
#
# Usage: transcribe.sh <episode_url> [output_file]
# Requires: GROQ_API_KEY env var, yt-dlp, ffmpeg, curl, jq
#
# Installed by `hermes reach setup xiaoyuzhou`.

set -euo pipefail

URL="${1:?Usage: transcribe.sh <episode_url> [output_file]}"
OUTPUT="${2:-/dev/stdout}"
GROQ_KEY="${GROQ_API_KEY:?GROQ_API_KEY not set — get a free key at console.groq.com}"

TMPDIR=$(mktemp -d /tmp/xiaoyuzhou-transcribe.XXXXXX)
trap 'rm -rf "$TMPDIR"' EXIT

echo "→ Downloading audio from $URL ..." >&2
yt-dlp -x --audio-format mp3 --audio-quality 5 -o "$TMPDIR/audio.%(ext)s" "$URL" >&2

# Find the downloaded file
AUDIO_FILE=$(ls "$TMPDIR"/audio.* 2>/dev/null | head -1)
if [ -z "$AUDIO_FILE" ]; then
  echo "ERROR: download failed" >&2
  exit 1
fi

echo "→ Transcoding to 16kHz mono WAV ..." >&2
ffmpeg -y -i "$AUDIO_FILE" -ar 16000 -ac 1 -f wav "$TMPDIR/audio.wav" 2>/dev/null

# Get duration in seconds
DURATION=$(ffprobe -v quiet -show_entries format=duration -of csv=p=0 "$TMPDIR/audio.wav" | cut -d. -f1)
echo "→ Duration: ${DURATION}s" >&2

# Groq Whisper has a 25MB file size limit (~25 min of 16kHz mono WAV).
# Slice into 20-minute segments to be safe.
SEGMENT_SECONDS=1200
NUM_SEGMENTS=$(( (DURATION + SEGMENT_SECONDS - 1) / SEGMENT_SECONDS ))

echo "→ Splitting into $NUM_SEGMENTS segment(s) of ${SEGMENT_SECONDS}s ..." >&2

FULL_TRANSCRIPT=""
for i in $(seq 0 $((NUM_SEGMENTS - 1))); do
  START=$((i * SEGMENT_SECONDS))
  SEGMENT_FILE="$TMPDIR/segment_${i}.wav"

  echo "→ Segment $((i + 1))/$NUM_SEGMENTS (offset ${START}s) ..." >&2
  ffmpeg -y -i "$TMPDIR/audio.wav" -ss "$START" -t "$SEGMENT_SECONDS" -c copy "$SEGMENT_FILE" 2>/dev/null

  # Check file size — Groq limit is ~25MB
  FILE_SIZE=$(stat -c%s "$SEGMENT_FILE" 2>/dev/null || stat -f%z "$SEGMENT_FILE" 2>/dev/null || echo 0)
  if [ "$FILE_SIZE" -gt 25000000 ]; then
    echo "WARNING: segment $((i + 1)) is ${FILE_SIZE} bytes (>25MB), may be rejected by Groq" >&2
  fi

  echo "→ Transcribing segment $((i + 1)) via Groq Whisper ..." >&2
  RESPONSE=$(curl -s -X POST "https://api.groq.com/openai/v1/audio/transcriptions" \
    -H "Authorization: Bearer $GROQ_KEY" \
    -F "file=@$SEGMENT_FILE" \
    -F "model=whisper-large-v3" \
    -F "language=zh" \
    -F "response_format=text")

  if [ -z "$RESPONSE" ]; then
    echo "ERROR: Groq API returned empty response for segment $((i + 1))" >&2
    FULL_TRANSCRIPT="${FULL_TRANSCRIPT}[segment $((i + 1)) transcription failed]\n"
  else
    FULL_TRANSCRIPT="${FULL_TRANSCRIPT}${RESPONSE}"
  fi

  # Rate limit: Groq allows ~7200s audio/hour. Sleep briefly between calls.
  if [ $i -lt $((NUM_SEGMENTS - 1)) ]; then
    sleep 2
  fi
done

# Output full transcript
echo -e "$FULL_TRANSCRIPT" > "$OUTPUT"
echo "→ Done. Transcript written to $OUTPUT" >&2