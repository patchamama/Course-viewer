#!/usr/bin/env bash
set -euo pipefail

PORT="${OC_PORT:-8080}"
DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
GITHUB_RAW="https://raw.githubusercontent.com/patchamama/Course-viewer/main"

echo "=== Course Viewer Launcher ==="
echo "Directory: $DIR"

# ── Version comparison (returns 0 if $1 > $2) ────────────────────────────────
_ver_gt() {
  local IFS=. a=($1) b=($2)
  for i in 0 1 2; do
    local ai="${a[$i]:-0}" bi="${b[$i]:-0}"
    (( 10#$ai > 10#$bi )) && return 0
    (( 10#$ai < 10#$bi )) && return 1
  done
  return 1
}

_download_file() {
  local file="$1"
  if command -v curl &>/dev/null; then
    curl -fsSL "$GITHUB_RAW/$file" -o "$DIR/$file" \
      || { echo "ERROR: failed to download $file"; exit 1; }
  elif command -v wget &>/dev/null; then
    wget -q -O "$DIR/$file" "$GITHUB_RAW/$file" \
      || { echo "ERROR: failed to download $file"; exit 1; }
  else
    echo "ERROR: curl or wget required to download app files."
    exit 1
  fi
}

# ── Auto-download or update app files ────────────────────────────────────────
_app_files_exist=true
for file in proxy.py course-viewer.html; do
  [[ ! -f "$DIR/$file" ]] && _app_files_exist=false && break
done

if ! $_app_files_exist; then
  # Fresh install — download without asking
  for file in proxy.py course-viewer.html; do
    if [[ ! -f "$DIR/$file" ]]; then
      echo "Downloading $file from GitHub..."
      _download_file "$file"
      echo "  OK $file"
    fi
  done
else
  # Files exist — check for a newer release
  _local_ver=$(grep -o "APP_VERSION = '[0-9][0-9.]*'" "$DIR/course-viewer.html" 2>/dev/null \
    | grep -o '[0-9][0-9.]*' | head -1)
  _latest_ver=$(curl -fsSL --connect-timeout 5 \
    "https://api.github.com/repos/patchamama/Course-viewer/releases/latest" 2>/dev/null \
    | grep -o '"tag_name":"v[^"]*"' | grep -o '[0-9][0-9.]*' | head -1)

  if [[ -n "$_latest_ver" && -n "$_local_ver" ]] && _ver_gt "$_latest_ver" "$_local_ver"; then
    echo ""
    echo "  New version available: v${_latest_ver}  (installed: v${_local_ver})"
    read -r -p "  Update now? [y/N] " _choice
    if [[ "$_choice" =~ ^[Yy]$ ]]; then
      for file in proxy.py course-viewer.html; do
        echo "  Updating $file..."
        _download_file "$file"
        echo "  OK $file"
      done
    fi
  fi
fi

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
# Kill any existing proxy on this port so the new directory always wins
if lsof -ti tcp:$PORT &>/dev/null; then
  lsof -ti tcp:$PORT | xargs kill -9 2>/dev/null
  sleep 0.5
fi

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
