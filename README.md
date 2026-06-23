# Course Viewer

> A local-first, offline-capable course companion app. Stream local videos with instant seeking, display multilingual subtitles, proxy Articulate Rise courses through localhost, and read PDFs and documents — all from a self-contained tool that runs entirely in your browser with zero cloud dependency.

## Features

### Video Player
- Stream local MP4, MKV, AVI, WebM with **instant seeking** via in-memory `moov` atom relocation — no re-encoding needed
- YouTube playback as automatic fallback when local file is missing or 0 bytes
- Detects YouTube IDs embedded in filenames: `[XXXXXXXXXXX]` and `(XXXXXXXXXXX)` patterns
- Auto-saves and restores **playback position per video** across sessions and app restarts

### Subtitles
- SRT and VTT support with automatic multi-language detection
- **Three display modes**: `Multiline` (full entry with line breaks), `Online` (last line only), `Online Join` (all lines merged into one)
- Full-text search across all subtitle content with jump-to-timestamp and auto-play on seek
- Customizable size, background color, background opacity, text color, and on-screen position (arrow controls)
- All style and position settings persisted in `localStorage`

### Tabbed Course Reader
- **Multiple documents open simultaneously** as tabs — PDFs, Markdown, HTML, EPUB, TXT
- Tabs persist per session, isolated per directory (localStorage keyed by directory hash so each course folder is independent)
- Switching tabs never reloads content (DOM preserved via `visibility:hidden`)
- **Image gallery tab**: all local images shown as thumbnails; click any image to view it full-size in a modal
- Articulate Rise course proxied through `localhost` with auto-login script injection and iframe restriction bypass
- Custom URL or local file input directly in the tab bar
- Per-document zoom controls

### Course Videos List
- Displays config videos and user-added external videos in one merged list
- **Auto-adds** YouTube / local videos to the list when opened via the add-video flow
- **Save Config** button merges all user-added videos into permanent `course-viewer.config.json`
- Delete user-added entries individually

### Markers and Notes
- Video timestamp markers with custom labels
- Sync bookmarks linking a video timestamp to a reader scroll position
- Reading position markers for documents
- Markdown notes editor with live preview (embedded or in bottom panel)

### Self-Bootstrapping Launcher
- `start.bat` (Windows) and `start.sh` (Mac/Linux) **auto-download** `proxy.py` and `course-viewer.html` from this GitHub repo if they are missing from the folder
- No installation required beyond Python 3

---

## Requirements

| Requirement | Notes |
|---|---|
| **Python 3.8+** | [python.org](https://python.org) — add to PATH on Windows |
| **Internet** (first run only) | Downloads `proxy.py` and `course-viewer.html` from GitHub |
| No other dependencies | Pure stdlib Python; all JS loaded from CDN |

---

## Quick Start

### Windows
1. Create a folder for your course content (videos, subtitles, PDFs…)
2. Download [`start.bat`](https://raw.githubusercontent.com/patchamama/Course-viewer/main/start.bat) into the folder
3. Double-click `start.bat`
4. App opens automatically at **http://localhost:8080/**

### Mac / Linux
1. Create a folder for your course content
2. Download [`start.sh`](https://raw.githubusercontent.com/patchamama/Course-viewer/main/start.sh) into the folder
3. Run: `bash start.sh`
4. App opens automatically at **http://localhost:8080/**

The launcher auto-downloads `proxy.py` and `course-viewer.html` from GitHub on the first run.

---

## Configuration

### Articulate Rise course (optional)

Create `course.readme.txt` in the course folder:

```
courseUrl: https://share.articulate.com/XXXX#/lessons/YYYY
coursePassword: YourPassword
```

### Video files

Place `.mp4` files in the course folder. The launcher auto-detects them and generates `course-viewer.config.json` on first run. Matching `.srt` / `.vtt` subtitle files are loaded automatically by filename.

YouTube ID in filename enables YouTube fallback when the local file is 0 bytes:
- `Video_Name_[XXXXXXXXXXX].mp4`
- `Video_Name_(XXXXXXXXXXX).mp4`

### No Articulate course?

If `course.readme.txt` is absent or has no `courseUrl`, the app **auto-detects local files** on startup:

| File type | Action |
|---|---|
| PDF, MD, HTML, EPUB, TXT | Opened as reader tabs |
| PNG, JPG, SVG, WebP, GIF | Collected into an image gallery tab |

### course-viewer.config.json schema

See [`course-viewer.config.json.example`](course-viewer.config.json.example) for the full structure.

---

## Project Structure

```
your-course-folder/
├── start.bat              ← Windows launcher
├── start.sh               ← Mac/Linux launcher
├── course-viewer.html             ← App UI (auto-downloaded from GitHub)
├── proxy.py               ← Local HTTP proxy server (auto-downloaded)
├── course.readme.txt      ← Course URL + password (optional, git-ignored)
├── course-viewer.config.json            ← Generated/saved config (git-ignored)
├── course-viewer.config.json.example    ← Schema reference
├── video_[YtId].mp4       ← Video files (git-ignored)
├── video_[YtId].srt       ← Subtitle files (git-ignored)
└── document.pdf           ← Local documents (git-ignored, auto-opened as tabs)
```

---

## Privacy

Everything runs on `localhost`. No data ever leaves your machine. The proxy routes Articulate Rise through `localhost:8080/proxy/` to bypass iframe restrictions, strips `X-Frame-Options` and `Content-Security-Policy` headers, and injects auto-login when a course password is configured.

---

## TODO

- [ ] **Elasticsearch subtitle indexing** — Index all subtitle cues into Elasticsearch for advanced cross-video full-text search. Support multilingual queries (including fuzzy matching, phrase search, and stemming). Expose a REST endpoint consumed by the subtitle search panel, enabling near-instant results across hundreds of hours of content in any language.
- [ ] EPUB reader integration (epub.js)
- [ ] Export notes to PDF / DOCX
- [ ] Keyboard shortcuts overlay
- [ ] Video speed control (0.5×–2×)
- [ ] Chapter markers derived from subtitle structure
- [ ] Right-to-left subtitle support (Arabic, Hebrew, Farsi)
- [ ] Dark / light theme toggle
- [ ] Playlist / watch queue with auto-advance to next video
- [ ] Offline PWA manifest
- [ ] Multi-device sync via WebSocket

---

## License

MIT — do whatever you want with it.
