# Changelog

All notable changes to Course Viewer are documented here.
Format follows [Keep a Changelog](https://keepachangelog.com/en/1.0.0/).
Versioning follows [Semantic Versioning](https://semver.org/).

---

## [1.0.0] - 2026-06-23

### Added
- **Video streaming** — local MP4/MKV/AVI/WebM with instant seeking via in-memory `moov` atom relocation
- **YouTube fallback** — automatic YouTube playback when local file is missing or smaller than 10 KB; detects YouTube ID from filename patterns `[ID]` and `(ID)`
- **Subtitle support** — SRT and VTT with multi-language detection; three display modes: `Multiline`, `Online` (last line), `Online Join` (all lines merged)
- **Subtitle search** — full-text search across all loaded subtitle cues; click to jump to timestamp with auto-play
- **Subtitle positioning** — on-screen position controls (↑↓←→⌂) persisted in localStorage
- **Tabbed Course Reader** — multiple documents open as tabs (PDF, Markdown, HTML, EPUB, TXT); tab DOM preserved via `visibility:hidden` so switching never reloads
- **Image gallery tab** — all local images as thumbnails; click to view full-size in a modal overlay
- **Directory browser tab** — navigate the server's directory tree, open files in the reader or player, open any subdirectory as a Course Viewer session
- **Articulate Rise proxy** — routes `share.articulate.com` through `localhost`, strips iframe restrictions, injects auto-login
- **Video position persistence** — saves playback position per video ID in localStorage; restores on re-open
- **Course Sessions** — save, load, and delete named course configurations (courseUrl, videos, reader tabs) in localStorage
- **Share URL** — generates a base64-encoded URL for GitHub Pages that encodes the current course config; recipients open it with no server needed
- **Open Directory** (Chrome/Edge) — File System Access API picker loads local course folder: videos, docs, subtitles, and images
- **Notes panel** — Markdown editor with live preview, import/export, zoom controls (both bottom panel and Panel B)
- **Video markers** — timestamp bookmarks with custom labels
- **Sync bookmarks** — link a video timestamp to a reader scroll position
- **Reading markers** — document position markers with labels
- **Save Config** — merges user-added videos into `course-viewer.config.json` via `POST /save-config`
- **Self-bootstrapping launchers** — `start.sh` (Mac/Linux) and `start.bat` (Windows) auto-download `proxy.py` and `course-viewer.html` from GitHub on first run
- **GitHub Pages deployment** — auto-deploys on push to `main`; live at https://patchamama.github.io/Course-viewer/course-viewer.html
- **Backend detection** — app detects whether the proxy is running and adjusts available features accordingly
- **App version badge** — version displayed in header, links to GitHub Releases
