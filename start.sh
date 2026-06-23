#!/usr/bin/env bash
set -euo pipefail

PORT="${OC_PORT:-8080}"
DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
GITHUB_RAW="https://raw.githubusercontent.com/patchamama/Course-viewer/main"

echo "=== Course Viewer Launcher ==="
echo "Directory: $DIR"

# ── Auto-download required app files from GitHub if missing ──────────────────
for file in proxy.py course-viewer.html; do
  if [[ ! -f "$DIR/$file" ]]; then
    echo "Downloading $file from GitHub..."
    if command -v curl &>/dev/null; then
      curl -fsSL "$GITHUB_RAW/$file" -o "$DIR/$file" \
        || { echo "ERROR: failed to download $file via curl"; exit 1; }
    elif command -v wget &>/dev/null; then
      wget -q -O "$DIR/$file" "$GITHUB_RAW/$file" \
        || { echo "ERROR: failed to download $file via wget"; exit 1; }
    else
      echo "ERROR: curl or wget required to download app files."
      exit 1
    fi
    echo "  OK $file"
  fi
done

# ── Read course config from course.readme.txt ────────────────────────────────
COURSE_URL=""
COURSE_PASSWORD=""
README="$DIR/course.readme.txt"

if [[ -f "$README" ]]; then
  while IFS= read -r line; do
    [[ "$line" =~ ^[[:space:]]*# ]] && continue
    [[ -z "${line// }" ]] && continue
    case "$line" in
      courseUrl:*)      COURSE_URL="${line#courseUrl:}";      COURSE_URL="${COURSE_URL# }" ;;
      coursePassword:*) COURSE_PASSWORD="${line#coursePassword:}"; COURSE_PASSWORD="${COURSE_PASSWORD# }" ;;
    esac
  done < "$README"
else
  echo "INFO: course.readme.txt not found — courseUrl and coursePassword will be empty"
fi

# ── Generate course-viewer.config.json (only if not already present) ───────────────────────
if [[ -f "$DIR/course-viewer.config.json" ]]; then
  echo "course-viewer.config.json already exists — skipping generation"
else
  entries=""
  first=1
  idx=0

  while IFS= read -r -d '' mp4; do
    filename="$(basename "$mp4")"
    title="${filename%.*}"
    idx=$((idx + 1))

    youtube_id=""
    if [[ "$filename" =~ \[([A-Za-z0-9_-]{11})\] ]]; then
      youtube_id="${BASH_REMATCH[1]}"
    elif [[ "$filename" =~ \(([A-Za-z0-9_-]{11})\) ]]; then
      youtube_id="${BASH_REMATCH[1]}"
    fi

    srt_basename=""
    for ext in srt vtt; do
      candidate="${mp4%.mp4}.${ext}"
      if [[ -f "$candidate" ]]; then
        srt_basename="$(basename "$candidate")"
        break
      fi
    done

    if [[ -n "$youtube_id" ]]; then
      vid_id="yt_${youtube_id}"
    else
      vid_id="video_${idx}"
    fi

    safe_title="${title//\"/\\\"}"
    safe_filename="${filename//\"/\\\"}"

    entry="{
      \"id\": \"${vid_id}\",
      \"title\": \"${safe_title}\",
      \"youtubeId\": \"${youtube_id}\",
      \"localFile\": \"${safe_filename}\",
      \"subtitleFile\": \"${srt_basename}\"
    }"

    if [[ $first -eq 1 ]]; then entries="$entry"; first=0
    else entries="$entries,
    $entry"; fi
  done < <(find "$DIR" -maxdepth 1 -name "*.mp4" -print0 | sort -z)

  cat > "$DIR/course-viewer.config.json" <<EOF
{
  "courseUrl": "$COURSE_URL",
  "coursePassword": "$COURSE_PASSWORD",
  "videos": [
    $entries
  ]
}
EOF
  echo "Generated course-viewer.config.json ($(find "$DIR" -maxdepth 1 -name "*.mp4" | wc -l | tr -d ' ') videos)"
fi

# ── Start server ─────────────────────────────────────────────────────────────
if ! command -v python3 &>/dev/null; then
  echo "ERROR: python3 is required. Install from https://python.org"
  exit 1
fi

echo ""
echo "Starting proxy server on port $PORT..."
echo "Open: http://localhost:$PORT/"

# Open browser after 1.5s
(sleep 1.5
  url="http://localhost:$PORT/"
  if command -v xdg-open &>/dev/null; then xdg-open "$url"
  elif command -v open &>/dev/null; then open "$url"
  fi
) &

cd "$DIR"
OC_PORT="$PORT" python3 proxy.py
