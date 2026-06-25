#!/usr/bin/env python3
"""
Local proxy server for Course Viewer (index.html)
- Serves static files from the project directory at /
- Proxies https://share.articulate.com at /proxy/
- Strips X-Frame-Options and CSP frame-ancestors headers
- Injects auto-login script into the proxied HTML
- Relocates MP4 moov atom to front in-memory for instant HTTP seeking
- Serves arbitrary local video files via /local-video?path=...
"""
import http.server
import socketserver
import struct
import urllib.request
import urllib.parse
import urllib.error
import ssl
import os
import json
import sys
import mimetypes
import re
import hashlib

PORT = int(os.environ.get('OC_PORT', '7478'))
APP_DIR    = os.path.dirname(os.path.abspath(__file__))   # fixed — where proxy.py lives
STATIC_DIR = os.path.normpath(os.environ.get('OC_STATIC_DIR') or APP_DIR)  # mutable — course dir
TARGET_HOST = 'share.articulate.com'
PROXY_PATH_PREFIX = '/proxy'
CDN_HOST = 'cdn.articulate.com'
CDN_PROXY_PATH_PREFIX = '/proxy-cdn'

# Read password from course-viewer.config.json
_password = 'Handout4EFB'
try:
    with open(os.path.join(STATIC_DIR, 'course-viewer.config.json'), 'r', encoding='utf-8-sig') as f:
        _cfg = json.load(f)
        _password = _cfg.get('coursePassword', _password)
except Exception:
    pass

AUTO_LOGIN_JS = f"""
<script>
(function(){{
  var _pw = {json.dumps(_password)};
  function tryLogin(){{
    var inp = document.querySelector('input[type="password"]');
    if(inp){{
      inp.value = _pw;
      ['input','change'].forEach(function(ev){{
        inp.dispatchEvent(new Event(ev,{{bubbles:true}}));
      }});
      setTimeout(function(){{
        var btns = Array.from(document.querySelectorAll('button'));
        var btn = btns.find(function(b){{return /next|submit|enter|continue|access/i.test(b.textContent);}});
        if(btn) btn.click();
      }}, 400);
    }} else {{
      setTimeout(tryLogin, 400);
    }}
  }}
  if(document.readyState==='loading'){{
    document.addEventListener('DOMContentLoaded', function(){{ setTimeout(tryLogin,800); }});
  }} else {{
    setTimeout(tryLogin,800);
  }}
}})();
</script>
"""

STRIP_HEADERS = {'x-frame-options', 'content-security-policy', 'transfer-encoding'}
SSL_CTX = ssl.create_default_context()

MIME_MAP = {
    '.html': 'text/html; charset=utf-8',
    '.htm': 'text/html; charset=utf-8',
    '.js': 'text/javascript',
    '.mjs': 'text/javascript',
    '.css': 'text/css',
    '.json': 'application/json',
    '.mp4': 'video/mp4',
    '.m4v': 'video/mp4',
    '.mov': 'video/quicktime',
    '.webm': 'video/webm',
    '.mkv': 'video/x-matroska',
    '.avi': 'video/x-msvideo',
    '.ogg': 'video/ogg',
    '.ogv': 'video/ogg',
    '.srt': 'text/plain; charset=utf-8',
    '.vtt': 'text/vtt; charset=utf-8',
    '.png': 'image/png',
    '.jpg': 'image/jpeg',
    '.jpeg': 'image/jpeg',
    '.gif': 'image/gif',
    '.svg': 'image/svg+xml',
    '.ico': 'image/x-icon',
    '.woff': 'font/woff',
    '.woff2': 'font/woff2',
    '.ttf': 'font/ttf',
    '.sh': 'text/plain',
    '.bat': 'text/plain',
    '.md': 'text/plain; charset=utf-8',
}

_VIDEO_EXTS = {'.mp4', '.m4v', '.mov', '.webm', '.mkv', '.avi', '.ogg', '.ogv'}

# ── MP4 moov-first relocation ─────────────────────────────────────────────────
_MP4_FIX_LIMIT = 1024 * 1024 * 1024   # 1 GB — skip larger files
_moov_cache: dict = {}                  # filepath → (mtime, bytes | None)
_MP4_CONTAINERS = {b'moov', b'trak', b'mdia', b'minf', b'stbl',
                   b'udta', b'edts', b'dinf', b'mvex', b'moof', b'traf'}


def _mp4_parse_boxes(data: bytes, start: int = 0, end: int = -1):
    if end < 0:
        end = len(data)
    pos = start
    boxes = []
    while pos + 8 <= end:
        sz = struct.unpack_from('>I', data, pos)[0]
        if sz == 1:
            if pos + 16 > end:
                break
            sz = struct.unpack_from('>Q', data, pos + 8)[0]
        if sz == 0:
            sz = end - pos
        typ = data[pos + 4: pos + 8]
        if sz < 8 or pos + sz > end:
            break
        boxes.append((typ, pos, sz))
        pos += sz
    return boxes


def _mp4_patch_offsets(buf: bytearray, delta: int, start: int = 0, end: int = -1):
    if end < 0:
        end = len(buf)
    for typ, pos, sz in _mp4_parse_boxes(buf, start, end):
        if typ == b'stco':
            count = struct.unpack_from('>I', buf, pos + 12)[0]
            for i in range(count):
                off = pos + 16 + i * 4
                val = struct.unpack_from('>I', buf, off)[0]
                struct.pack_into('>I', buf, off, val + delta)
        elif typ == b'co64':
            count = struct.unpack_from('>I', buf, pos + 12)[0]
            for i in range(count):
                off = pos + 16 + i * 8
                val = struct.unpack_from('>Q', buf, off)[0]
                struct.pack_into('>Q', buf, off, val + delta)
        elif typ in _MP4_CONTAINERS:
            _mp4_patch_offsets(buf, delta, pos + 8, pos + sz)


def _mp4_moov_first(filepath: str):
    try:
        fsize = os.path.getsize(filepath)
        if fsize > _MP4_FIX_LIMIT:
            return None
        with open(filepath, 'rb') as f:
            raw = f.read()
    except OSError:
        return None

    boxes = _mp4_parse_boxes(raw)
    types = [b[0] for b in boxes]

    if b'moov' not in types or b'mdat' not in types:
        return None

    moov_idx = types.index(b'moov')
    mdat_idx = types.index(b'mdat')

    if moov_idx < mdat_idx:
        return None  # already moov-first

    _, moov_pos, moov_sz = boxes[moov_idx]
    delta = moov_sz

    moov_buf = bytearray(raw[moov_pos: moov_pos + moov_sz])
    _mp4_patch_offsets(moov_buf, delta)

    out = bytearray()
    if b'ftyp' in types:
        _, fp, fs = boxes[types.index(b'ftyp')]
        out += raw[fp: fp + fs]
    out += moov_buf
    for typ, bp, bs in boxes:
        if typ in (b'ftyp', b'moov'):
            continue
        out += raw[bp: bp + bs]

    return bytes(out)


def _get_mp4_content(filepath: str):
    try:
        mtime = os.path.getmtime(filepath)
        hit = _moov_cache.get(filepath)
        if hit and hit[0] == mtime:
            return hit[1]
        result = _mp4_moov_first(filepath)
        _moov_cache[filepath] = (mtime, result)
        label = 'relocated moov→front' if result else 'moov already first'
        sys.stderr.write(f'[mp4] {label}: {os.path.basename(filepath)}\n')
        return result
    except Exception:
        return None


# ─────────────────────────────────────────────────────────────────────────────

class Handler(http.server.BaseHTTPRequestHandler):

    def do_GET(self):
        if self.path.startswith(CDN_PROXY_PATH_PREFIX + '/') or self.path == CDN_PROXY_PATH_PREFIX:
            self._proxy(CDN_HOST, CDN_PROXY_PATH_PREFIX)
        elif self.path.startswith(PROXY_PATH_PREFIX + '/') or self.path == PROXY_PATH_PREFIX:
            self._proxy(TARGET_HOST, PROXY_PATH_PREFIX)
        elif self.path == '/list-local-files' or self.path.startswith('/list-local-files?'):
            self._list_local_files()
        elif self.path.startswith('/list-directory'):
            self._list_directory()
        elif self.path.startswith('/local-video'):
            self._serve_local_video()
        elif self.path.startswith('/serve-file'):
            self._serve_file_absolute()
        elif self.path.startswith('/api/browse'):
            self._browse_dir()
        elif self.path == '/api/list-nearby':
            self._list_nearby()
        elif self.path.startswith('/api/course-structure'):
            self._course_structure()
        elif self.path.startswith('/api/zip-list'):
            self._zip_list()
        elif self.path.startswith('/api/zip-extract'):
            self._zip_extract()
        elif self.path.startswith('/api/epub-html'):
            self._epub_html()
        elif self.path.startswith('/api/docx-html'):
            self._docx_html()
        elif self.path.startswith('/api/search'):
            self._search()
        elif self.path.startswith('/api/file-content'):
            self._file_content()
        elif self.path.startswith('/api/check-version'):
            self._check_version()
        elif self.path.startswith('/api/self-update'):
            self._self_update()
        else:
            self._static()

    def do_HEAD(self):
        self.do_GET()

    def do_POST(self):
        if self.path == '/save-config':
            self._handle_save_config()
            return
        if self.path == '/api/switch-dir':
            self._handle_switch_dir()
            return
        if self.path.startswith(CDN_PROXY_PATH_PREFIX + '/'):
            self._proxy(CDN_HOST, CDN_PROXY_PATH_PREFIX)
        elif self.path.startswith(PROXY_PATH_PREFIX + '/'):
            self._proxy(TARGET_HOST, PROXY_PATH_PREFIX)
        else:
            self._proxy(TARGET_HOST, '')

    def do_OPTIONS(self):
        self.send_response(200)
        self.send_header('Access-Control-Allow-Origin', '*')
        self.send_header('Access-Control-Allow-Methods', 'GET, POST, HEAD, OPTIONS')
        self.send_header('Access-Control-Allow-Headers', 'Content-Type, Authorization, Accept')
        self.end_headers()

    # ─── JSON response helper ─────────────────────────────────────────────────
    def _json(self, data):
        resp = json.dumps(data, ensure_ascii=False).encode('utf-8')
        self.send_response(200)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Content-Length', str(len(resp)))
        self.send_header('Access-Control-Allow-Origin', '*')
        self.send_header('Cache-Control', 'no-cache')
        self.end_headers()
        if self.command != 'HEAD':
            self.wfile.write(resp)

    # ─── Course structure detection ───────────────────────────────────────────
    def _course_structure(self):
        import re
        VIDEO_EXTS = {'.mp4', '.mkv', '.avi', '.webm', '.m4v', '.mov'}
        DOC_EXTS   = {'.pdf', '.md', '.html', '.htm', '.epub', '.txt', '.zip', '.docx', '.doc', '.rtf'}
        EXCL_NAMES = {
            'course-viewer.html', 'proxy.py', 'start.bat', 'start.sh',
            'index.html', 'readme.md', 'demo.html', 'course-viewer.config.json',
            'course-viewer.config.json.example',
        }
        result = {'pattern': None, 'chapters': []}
        try:
            all_names = sorted(
                [n for n in os.listdir(STATIC_DIR)
                 if not n.startswith('.') and not n.startswith('_')],
                key=str.lower
            )
        except Exception:
            self._json(result)
            return

        subdirs    = [n for n in all_names if os.path.isdir(os.path.join(STATIC_DIR, n))]
        root_files = [n for n in all_names
                      if os.path.isfile(os.path.join(STATIC_DIR, n))
                      and n.lower() not in EXCL_NAMES]

        # Pattern A — numeric prefix groups (01, 02, 03 …)
        pfx_re = re.compile(r'^(\d+)')
        groups  = {}
        for fname in root_files:
            m = pfx_re.match(fname)
            if m:
                groups.setdefault(m.group(1), []).append(fname)

        SUB_EXTS = {'.srt', '.vtt'}

        def _has_sub(base_path):
            for se in SUB_EXTS:
                if os.path.isfile(base_path + se):
                    return True
                # language-coded: base.en.srt, base.de.srt, etc.
                import glob as _glob
                if _glob.glob(base_path + '.*' + se):
                    return True
            return False

        if len(groups) >= 2:
            chapters = []
            for pfx in sorted(groups, key=lambda x: int(x)):
                files  = sorted(groups[pfx])
                videos = []
                for f in files:
                    if os.path.splitext(f)[1].lower() not in VIDEO_EXTS:
                        continue
                    base = os.path.join(STATIC_DIR, os.path.splitext(f)[0])
                    videos.append({'name': f, 'rel': f, 'hasSub': _has_sub(base)})
                docs   = [{'name': f, 'rel': f}
                          for f in files if os.path.splitext(f)[1].lower() in DOC_EXTS]
                if not videos and not docs:
                    continue
                src   = videos[0]['name'] if videos else docs[0]['name']
                title = os.path.splitext(src)[0]
                chapters.append({'title': title, 'videos': videos, 'docs': docs,
                                 'subpath': None, 'hasSubs': False})
            if chapters:
                result = {'pattern': 'flat-prefix', 'chapters': chapters}

        elif len(subdirs) >= 2:
            chapters = []
            for sub in subdirs:
                sub_abs = os.path.join(STATIC_DIR, sub)
                try:
                    sub_entries = sorted(os.listdir(sub_abs), key=str.lower)
                except Exception:
                    sub_entries = []
                videos, docs = [], []
                has_subsubs = False
                for f in sub_entries:
                    if f.startswith('.') or f.startswith('_'):
                        continue
                    full = os.path.join(sub_abs, f)
                    if os.path.isdir(full):
                        has_subsubs = True
                        continue
                    ext = os.path.splitext(f)[1].lower()
                    rel = sub + '/' + f
                    if ext in VIDEO_EXTS:
                        base = os.path.join(sub_abs, os.path.splitext(f)[0])
                        videos.append({'name': f, 'rel': rel, 'hasSub': _has_sub(base)})
                    elif ext in DOC_EXTS:
                        docs.append({'name': f, 'rel': rel})
                chapters.append({'title': sub, 'videos': videos, 'docs': docs,
                                 'subpath': sub, 'hasSubs': has_subsubs})
            if chapters:
                result = {'pattern': 'subdirectory', 'chapters': chapters}

        self._json(result)

    # ─── ZIP browser: list contents ───────────────────────────────────────────
    def _zip_list(self):
        import zipfile
        params = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
        rel    = urllib.parse.unquote(params.get('path', [''])[0])
        absp   = os.path.normpath(os.path.join(STATIC_DIR, rel))
        if not absp.startswith(STATIC_DIR) or not os.path.isfile(absp):
            self.send_error(404)
            return
        try:
            with zipfile.ZipFile(absp) as zf:
                files = sorted(n for n in zf.namelist() if not n.endswith('/'))
            self._json({'files': files})
        except Exception:
            self.send_error(400, 'Invalid ZIP')

    # ─── ZIP browser: extract and serve a single entry ────────────────────────
    def _zip_extract(self):
        import zipfile
        params = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
        rel    = urllib.parse.unquote(params.get('zip',   [''])[0])
        entry  = urllib.parse.unquote(params.get('entry', [''])[0])
        absp   = os.path.normpath(os.path.join(STATIC_DIR, rel))
        if not absp.startswith(STATIC_DIR) or not os.path.isfile(absp):
            self.send_error(404)
            return
        try:
            with zipfile.ZipFile(absp) as zf:
                names = zf.namelist()
                entry_norm = entry.replace('\\', '/')
                # 1. Exact match
                actual = entry_norm if entry_norm in names else None
                # 2. Case-insensitive full-path match
                if actual is None:
                    el = entry_norm.lower()
                    matches = [n for n in names if not n.endswith('/') and n.lower() == el]
                    if matches: actual = matches[0]
                # 3. Same basename anywhere in ZIP
                if actual is None:
                    bn = entry_norm.rsplit('/', 1)[-1].lower()
                    matches = [n for n in names if not n.endswith('/') and n.rsplit('/', 1)[-1].lower() == bn]
                    if matches: actual = matches[0]
                if actual is None:
                    self.send_error(404, 'Entry not found in ZIP')
                    return
                data = zf.read(actual)
            ext  = os.path.splitext(actual)[1].lower()
            mime = MIME_MAP.get(ext, mimetypes.guess_type(actual)[0] or 'application/octet-stream')
            self.send_response(200)
            self.send_header('Content-Type', mime)
            self.send_header('Content-Length', str(len(data)))
            self.send_header('Access-Control-Allow-Origin', '*')
            self.send_header('Content-Disposition',
                             f'inline; filename="{os.path.basename(actual)}"')
            self.end_headers()
            if self.command != 'HEAD':
                self.wfile.write(data)
        except KeyError:
            self.send_error(404, 'Entry not found in ZIP')
        except Exception as e:
            self.send_error(500, str(e))

    # ─── EPUB viewer: extract + concatenate HTML content ─────────────────────
    def _epub_html(self):
        import zipfile, re as _re
        params = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
        rel    = urllib.parse.unquote(params.get('path', [''])[0])
        absp   = os.path.normpath(os.path.join(STATIC_DIR, rel))
        if not absp.startswith(STATIC_DIR) or not os.path.isfile(absp):
            self.send_error(404)
            return

        try:
            with zipfile.ZipFile(absp) as zf:
                names = zf.namelist()
                name_set = set(names)
                # basename.lower() → first actual path (for fallback when path doesn't resolve)
                bn_map: dict = {}
                for _n in names:
                    if not _n.endswith('/'):
                        _bn = _n.rsplit('/', 1)[-1].lower()
                        if _bn not in bn_map:
                            bn_map[_bn] = _n

                def _rewrite_assets(content, base_dir, epub_rel):
                    """Rewrite relative src/href URLs to /api/zip-extract; verify against ZIP."""
                    def resolve(url):
                        if _re.match(r'(https?:|data:|#|/)', url):
                            return None
                        path = (base_dir + '/' + url) if base_dir else url
                        segs = []
                        for seg in path.split('/'):
                            if seg == '..':
                                if segs: segs.pop()
                            elif seg and seg != '.':
                                segs.append(seg)
                        resolved = '/'.join(segs)
                        # Verify the path actually exists in the ZIP
                        if resolved not in name_set:
                            bn = resolved.rsplit('/', 1)[-1].lower()
                            resolved = bn_map.get(bn, resolved)
                        return resolved
                    def replace(m):
                        attr, url = m.group(1), m.group(3)
                        q = m.group(2)
                        resolved = resolve(url)
                        if resolved is None:
                            return m.group(0)
                        new_url = ('/api/zip-extract?zip=' + urllib.parse.quote(epub_rel, safe='')
                                   + '&entry=' + urllib.parse.quote(resolved, safe=''))
                        return f'{attr}={q}{new_url}{q}'
                    return _re.sub(r'(src|href)=(["\'])([^"\']+)\2', replace, content)

                content_files = sorted(
                    [n for n in names
                     if _re.search(r'\.(html|htm|xhtml)$', n, _re.I)
                     and 'META-INF' not in n
                     and 'toc' not in n.lower()],
                    key=lambda x: x
                )
                if not content_files:
                    self.send_error(404, 'No HTML content in EPUB')
                    return
                parts = []
                for cf in content_files:
                    try:
                        raw = zf.read(cf).decode('utf-8', errors='replace')
                        m = _re.search(r'<body[^>]*>(.*?)</body>', raw, _re.DOTALL | _re.I)
                        content = m.group(1) if m else raw
                        base_dir = cf.rsplit('/', 1)[0] if '/' in cf else ''
                        content = _rewrite_assets(content, base_dir, rel)
                        parts.append(content)
                    except Exception:
                        pass
            title = os.path.splitext(os.path.basename(rel))[0]
            combined = '\n<hr style="border:none;border-top:1px solid rgba(255,255,255,.1);margin:2em 0">\n'.join(parts)
            doc = (
                '<!DOCTYPE html><html><head><meta charset="utf-8">'
                f'<title>{title}</title>'
                '<style>'
                'body{font-family:Georgia,serif;max-width:720px;margin:0 auto;padding:24px;line-height:1.7;background:#0f1117;color:#e2e8f0}'
                'html.light body{background:#fff;color:#1a1a1a}'
                'img{max-width:100%}'
                'h1,h2,h3{color:#a5b4fc} html.light h1,html.light h2,html.light h3{color:#312e81}'
                'a{color:#7c8cf8} html.light a{color:#4338ca}'
                'hr{border:none;border-top:1px solid rgba(255,255,255,.12)} html.light hr{border-top-color:rgba(0,0,0,.12)}'
                '</style>'
                # Sync parent document's html class (light/dark) into this iframe — same origin
                '<script>(function(){'
                'function sync(){try{document.documentElement.className=parent.document.documentElement.className;}catch(e){}}'
                'sync();'
                'try{new MutationObserver(sync).observe(parent.document.documentElement,{attributes:true,attributeFilter:["class"]});}catch(e){}'
                '})();</script>'
                '</head>'
                f'<body>{combined}</body></html>'
            )
            data = doc.encode('utf-8')
            self.send_response(200)
            self.send_header('Content-Type', 'text/html; charset=utf-8')
            self.send_header('Content-Length', str(len(data)))
            self.send_header('Access-Control-Allow-Origin', '*')
            self.end_headers()
            if self.command != 'HEAD':
                self.wfile.write(data)
        except Exception as e:
            self.send_error(500, str(e))

    # ─── DOCX / DOC / RTF → HTML viewer ──────────────────────────────────────
    def _docx_html(self):
        import re as _re, zipfile
        params = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
        rel    = urllib.parse.unquote(params.get('path', [''])[0])
        absp   = os.path.normpath(os.path.join(STATIC_DIR, rel))
        if not absp.startswith(STATIC_DIR) or not os.path.isfile(absp):
            self.send_error(404); return
        ext = os.path.splitext(absp)[1].lower()
        title = os.path.splitext(os.path.basename(rel))[0]
        try:
            if ext in {'.docx', '.doc'}:
                try:
                    import mammoth
                    with open(absp, 'rb') as fh:
                        result = mammoth.convert_to_html(fh)
                    html_body = result.value
                except ImportError:
                    # Fallback: extract raw text from docx XML
                    try:
                        with zipfile.ZipFile(absp) as zf:
                            if 'word/document.xml' in zf.namelist():
                                xml = zf.read('word/document.xml').decode('utf-8', errors='replace')
                                text = _re.sub(r'<[^>]+>', ' ', xml)
                                text = _re.sub(r'\s+', ' ', text).strip()
                                html_body = (f'<div style="color:#fbbf24;font-size:11px;margin-bottom:12px">'
                                             f'⚠ Install mammoth (<code>pip install mammoth</code>) for rich rendering</div>'
                                             f'<pre style="white-space:pre-wrap;font-family:inherit">{text[:80000]}</pre>')
                            else:
                                html_body = '<p style="color:#f87171">Cannot read file — not a valid DOCX.</p>'
                    except Exception as e2:
                        html_body = f'<p style="color:#f87171">Error reading file: {e2}</p>'
            elif ext == '.rtf':
                with open(absp, encoding='utf-8', errors='replace') as fh:
                    raw = fh.read()
                # Best-effort RTF stripping (no dependencies)
                text = _re.sub(r'\\[a-z]+\-?\d*[ ]?', ' ', raw)
                text = _re.sub(r'[{}]', '', text)
                text = _re.sub(r'\\[^a-z]', '', text)
                text = _re.sub(r'\s+', ' ', text).strip()[:80000]
                html_body = f'<pre style="white-space:pre-wrap;font-family:inherit">{text}</pre>'
            else:
                html_body = '<p style="color:#f87171">Unsupported format.</p>'
        except Exception as e:
            html_body = f'<p style="color:#f87171">Error: {e}</p>'

        doc = (
            '<!DOCTYPE html><html><head><meta charset="utf-8">'
            f'<title>{title}</title>'
            '<style>'
            'body{font-family:Georgia,serif;max-width:800px;margin:0 auto;padding:24px;line-height:1.7;background:#0f1117;color:#e2e8f0}'
            'html.light body{background:#fff;color:#1a1a1a}'
            'h1,h2,h3{color:#a5b4fc} html.light h1,html.light h2,html.light h3{color:#312e81}'
            'img{max-width:100%} table{border-collapse:collapse;width:100%} td,th{border:1px solid #2d3154;padding:6px}'
            '</style>'
            '<script>(function(){function sync(){try{document.documentElement.className=parent.document.documentElement.className;}catch(e){}}'
            'sync();try{new MutationObserver(sync).observe(parent.document.documentElement,{attributes:true,attributeFilter:["class"]});}catch(e){}'
            '})();</script>'
            '</head>'
            f'<body>{html_body}</body></html>'
        )
        data = doc.encode('utf-8')
        self.send_response(200)
        self.send_header('Content-Type', 'text/html; charset=utf-8')
        self.send_header('Content-Length', str(len(data)))
        self.send_header('Access-Control-Allow-Origin', '*')
        self.end_headers()
        if self.command != 'HEAD':
            self.wfile.write(data)

    # ─── List directory contents for the directory browser ────────────────────
    def _list_directory(self):
        parsed = urllib.parse.urlparse(self.path)
        params = urllib.parse.parse_qs(parsed.query)
        rel = urllib.parse.unquote(params.get('path', [''])[0]).strip('/')
        target = os.path.normpath(os.path.join(STATIC_DIR, rel)) if rel else STATIC_DIR
        # Prevent path traversal outside STATIC_DIR's parent tree (allow going up from STATIC_DIR)
        # We allow browsing parent dirs — user explicitly requested it
        if not os.path.isdir(target):
            self.send_error(404, 'Directory not found')
            return
        entries = {'path': rel, 'dirs': [], 'files': []}
        try:
            for name in sorted(os.listdir(target), key=str.lower):
                if name.startswith('.'):
                    continue
                full = os.path.join(target, name)
                if os.path.isdir(full):
                    entries['dirs'].append(name)
                elif os.path.isfile(full):
                    ext = os.path.splitext(name)[1].lower()
                    size = os.path.getsize(full)
                    entries['files'].append({'name': name, 'ext': ext, 'size': size})
        except PermissionError:
            pass
        # Indicate if parent navigation is available
        entries['hasParent'] = target != os.path.abspath(os.sep)
        resp = json.dumps(entries, ensure_ascii=False).encode('utf-8')
        self.send_response(200)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Content-Length', str(len(resp)))
        self.send_header('Access-Control-Allow-Origin', '*')
        self.send_header('Cache-Control', 'no-cache')
        self.end_headers()
        if self.command != 'HEAD':
            self.wfile.write(resp)

    # ─── List local docs and images for auto-open in Course Reader ───────────
    def _list_local_files(self):
        DOC_EXTS = {'.pdf', '.md', '.html', '.htm', '.epub', '.txt'}
        IMG_EXTS = {'.png', '.jpg', '.jpeg', '.gif', '.svg', '.webp'}
        EXCLUDE_NAMES = {
            'course-viewer.html', 'index.html', 'open_course.html', 'course.readme.txt',
            'course-viewer.config.json', 'course-viewer.config.json.example', 'readme.md'
        }
        EXCLUDE_EXTS = {'.py', '.sh', '.bat', '.gitignore', '.json'}
        # Optional path param for subdirectory
        parsed = urllib.parse.urlparse(self.path)
        params = urllib.parse.parse_qs(parsed.query)
        rel = urllib.parse.unquote(params.get('path', [''])[0]).strip('/')
        scan_dir = os.path.normpath(os.path.join(STATIC_DIR, rel)) if rel else STATIC_DIR
        if not os.path.isdir(scan_dir):
            scan_dir = STATIC_DIR
        docs = []
        images = []
        try:
            for fname in sorted(os.listdir(scan_dir)):
                if fname.startswith('.') or fname.startswith('_'):
                    continue
                if fname.lower() in EXCLUDE_NAMES:
                    continue
                fpath = os.path.join(scan_dir, fname)
                if not os.path.isfile(fpath):
                    continue
                ext = os.path.splitext(fname)[1].lower()
                if ext in EXCLUDE_EXTS:
                    continue
                if ext in DOC_EXTS:
                    docs.append(fname)
                elif ext in IMG_EXTS:
                    images.append(fname)
        except Exception:
            pass
        resp = json.dumps({'docs': docs, 'images': images}, ensure_ascii=False).encode('utf-8')
        self.send_response(200)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Content-Length', str(len(resp)))
        self.send_header('Access-Control-Allow-Origin', '*')
        self.send_header('Cache-Control', 'no-cache')
        self.end_headers()
        if self.command != 'HEAD':
            self.wfile.write(resp)

    # ─── Serve arbitrary local video file ────────────────────────────────────
    def _serve_local_video(self):
        parsed = urllib.parse.urlparse(self.path)
        params = urllib.parse.parse_qs(parsed.query)
        file_path = urllib.parse.unquote(params.get('path', [''])[0])
        if not file_path:
            self.send_error(400, 'Missing path parameter')
            return
        file_path = os.path.normpath(file_path)
        if not os.path.isfile(file_path):
            self.send_error(404, 'File not found')
            return
        ext = os.path.splitext(file_path)[1].lower()
        if ext not in _VIDEO_EXTS:
            self.send_error(403, 'Only video files served here')
            return
        mime = MIME_MAP.get(ext, 'video/mp4')
        self._send_file(file_path, mime)

    def _handle_save_config(self):
        try:
            length = int(self.headers.get('Content-Length', 0))
            body = self.rfile.read(length)
            cfg = json.loads(body)
            cfg.pop('_dirId', None)
            config_path = os.path.join(STATIC_DIR, 'course-viewer.config.json')
            with open(config_path, 'w', encoding='utf-8') as f:
                json.dump(cfg, f, ensure_ascii=False, indent=2)
            resp = b'{"ok":true}'
            self.send_response(200)
            self.send_header('Content-Type', 'application/json')
            self.send_header('Content-Length', str(len(resp)))
            self.send_header('Access-Control-Allow-Origin', '*')
            self.end_headers()
            self.wfile.write(resp)
        except Exception as e:
            self.send_error(500, str(e))

    # ─── Static file handler ──────────────────────────────────────────────────
    # App files (course-viewer.html, proxy.py, demo.html, …) always come from
    # APP_DIR so they remain available after STATIC_DIR switches to a course
    # directory that may not contain a copy of those files.
    _APP_FILES = frozenset({
        'course-viewer.html', 'proxy.py', 'demo.html',
        'course-viewer.config.json.example',
    })

    def _static(self):
        raw = self.path.split('?')[0].split('#')[0]
        raw = urllib.parse.unquote(raw)
        if raw in ('', '/'):
            raw = '/course-viewer.html'

        name = os.path.basename(raw.lstrip('/'))
        if name in self._APP_FILES:
            base_dir = APP_DIR
        else:
            base_dir = STATIC_DIR

        filepath = os.path.normpath(os.path.join(base_dir, raw.lstrip('/')))
        if not filepath.startswith(base_dir):
            self.send_error(403)
            return
        if os.path.isdir(filepath):
            filepath = os.path.join(filepath, 'index.html')

        # Always inject _dirId/_dirName/_dirPath into course-viewer.config.json,
        # synthesising a minimal config on the fly when the file doesn't exist yet.
        if os.path.basename(filepath) == 'course-viewer.config.json' and \
           os.path.normpath(os.path.dirname(filepath)) == os.path.normpath(STATIC_DIR):
            cfg = {'courseUrl': '', 'coursePassword': '', 'videos': []}
            if os.path.isfile(filepath):
                try:
                    with open(filepath, 'r', encoding='utf-8-sig') as f:
                        loaded = json.load(f)
                    if isinstance(loaded, dict):
                        cfg.update(loaded)
                except Exception:
                    pass
            cfg['_dirId']   = hashlib.md5(STATIC_DIR.encode()).hexdigest()[:8]
            cfg['_dirName'] = os.path.basename(STATIC_DIR)
            cfg['_dirPath'] = STATIC_DIR
            data = json.dumps(cfg, ensure_ascii=False, indent=2).encode('utf-8')
            self.send_response(200)
            self.send_header('Content-Type', 'application/json')
            self.send_header('Content-Length', str(len(data)))
            self.send_header('Cache-Control', 'no-cache')
            self.send_header('Access-Control-Allow-Origin', '*')
            self.end_headers()
            if self.command != 'HEAD':
                self.wfile.write(data)
            return

        if not os.path.isfile(filepath):
            self.send_error(404, f'Not found: {raw}')
            return

        ext = os.path.splitext(filepath)[1].lower()
        mime = MIME_MAP.get(ext, mimetypes.guess_type(filepath)[0] or 'application/octet-stream')
        self._send_file(filepath, mime)

    # ─── Serve any local file by absolute path ───────────────────────────────
    def _serve_file_absolute(self):
        parsed = urllib.parse.urlparse(self.path)
        params = urllib.parse.parse_qs(parsed.query)
        file_path = urllib.parse.unquote(params.get('path', [''])[0])
        if not file_path:
            self.send_error(400, 'Missing path parameter')
            return
        file_path = os.path.normpath(file_path)
        if not os.path.isfile(file_path):
            self.send_error(404, 'File not found')
            return
        ext = os.path.splitext(file_path)[1].lower()
        mime = MIME_MAP.get(ext, mimetypes.guess_type(file_path)[0] or 'application/octet-stream')
        self._send_file(file_path, mime)

    # ─── Directory tree browser ────────────────────────────────────────────────
    def _browse_dir(self):
        VIDEO_EXTS = {'.mp4', '.mkv', '.avi', '.webm', '.m4v', '.mov'}
        DOC_EXTS   = {'.pdf', '.md', '.html', '.htm', '.epub', '.txt'}
        EXCL_NAMES = {'course-viewer.html', 'proxy.py', 'start.bat', 'start.sh',
                      'index.html', 'readme.md', 'demo.html'}
        parsed = urllib.parse.urlparse(self.path)
        params = urllib.parse.parse_qs(parsed.query)
        req    = urllib.parse.unquote(params.get('path', [''])[0]).strip()
        target = req if req and os.path.isdir(req) else os.path.dirname(STATIC_DIR)
        parent_path = os.path.dirname(target)
        parent = parent_path if parent_path and parent_path != target else None
        entries = []
        try:
            for name in sorted(os.listdir(target), key=str.lower):
                if name.startswith('.') or name.startswith('_'):
                    continue
                full = os.path.join(target, name)
                if not os.path.isdir(full):
                    continue
                has_config  = os.path.isfile(os.path.join(full, 'course-viewer.config.json'))
                has_content = has_config
                if not has_content:
                    try:
                        for fname in os.listdir(full):
                            ext = os.path.splitext(fname)[1].lower()
                            if fname.lower() not in EXCL_NAMES and (ext in VIDEO_EXTS or ext in DOC_EXTS):
                                has_content = True
                                break
                    except PermissionError:
                        pass
                entries.append({
                    'name':      name,
                    'path':      full,
                    'hasContent': has_content,
                    'hasConfig':  has_config,
                    'isCurrent':  os.path.normpath(full) == os.path.normpath(STATIC_DIR),
                })
        except PermissionError:
            pass
        result = {
            'path':    target,
            'name':    os.path.basename(target) or target,
            'parent':  parent,
            'entries': entries,
        }
        resp = json.dumps(result, ensure_ascii=False).encode('utf-8')
        self.send_response(200)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Content-Length', str(len(resp)))
        self.send_header('Access-Control-Allow-Origin', '*')
        self.send_header('Cache-Control', 'no-cache')
        self.end_headers()
        self.wfile.write(resp)

    # ─── Scan sibling/parent/child dirs for course configs ────────────────────
    def _list_nearby(self):
        parent = os.path.dirname(STATIC_DIR)
        def _entry(path):
            return {
                'path': path,
                'name': os.path.basename(path) or path,
                'hasConfig': os.path.isfile(os.path.join(path, 'course-viewer.config.json')),
            }
        siblings, children = [], []
        if parent and os.path.isdir(parent) and parent != STATIC_DIR:
            try:
                for name in sorted(os.listdir(parent), key=str.lower):
                    full = os.path.join(parent, name)
                    if full == STATIC_DIR or not os.path.isdir(full) or name.startswith('.'):
                        continue
                    has_cfg = os.path.isfile(os.path.join(full, 'course-viewer.config.json'))
                    has_launcher = os.path.isfile(os.path.join(full, 'start.bat')) or \
                                   os.path.isfile(os.path.join(full, 'start.sh'))
                    if has_cfg or has_launcher:
                        siblings.append(_entry(full))
            except PermissionError:
                pass
        try:
            for name in sorted(os.listdir(STATIC_DIR), key=str.lower):
                full = os.path.join(STATIC_DIR, name)
                if not os.path.isdir(full) or name.startswith('.') or name.startswith('_'):
                    continue
                if os.path.isfile(os.path.join(full, 'course-viewer.config.json')) or \
                   os.path.isfile(os.path.join(full, 'start.bat')):
                    children.append(_entry(full))
        except PermissionError:
            pass
        result = {
            'current': _entry(STATIC_DIR),
            'parent':  _entry(parent) if parent and parent != STATIC_DIR else None,
            'siblings': siblings,
            'children': children,
        }
        resp = json.dumps(result, ensure_ascii=False).encode('utf-8')
        self.send_response(200)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Content-Length', str(len(resp)))
        self.send_header('Access-Control-Allow-Origin', '*')
        self.send_header('Cache-Control', 'no-cache')
        self.end_headers()
        if self.command != 'HEAD':
            self.wfile.write(resp)

    # ─── Switch STATIC_DIR at runtime ─────────────────────────────────────────
    def _handle_switch_dir(self):
        global STATIC_DIR
        try:
            length = int(self.headers.get('Content-Length', 0))
            body = self.rfile.read(length)
            data = json.loads(body)
            new_path = os.path.normpath(data.get('path', ''))
            if not os.path.isdir(new_path):
                self.send_error(400, 'Directory not found: ' + new_path)
                return
            STATIC_DIR = new_path
            resp = json.dumps({
                'ok': True,
                'dirId':   hashlib.md5(STATIC_DIR.encode()).hexdigest()[:8],
                'dirName': os.path.basename(STATIC_DIR),
                'dirPath': STATIC_DIR,
            }).encode()
            self.send_response(200)
            self.send_header('Content-Type', 'application/json')
            self.send_header('Content-Length', str(len(resp)))
            self.send_header('Access-Control-Allow-Origin', '*')
            self.end_headers()
            self.wfile.write(resp)
        except Exception as e:
            self.send_error(500, str(e))

    # ─── Shared file sender (Range + moov relocation) ─────────────────────────
    def _send_file(self, filepath: str, mime: str):
        CHUNK = 512 * 1024

        ext = os.path.splitext(filepath)[1].lower()
        mp4_data = None
        # HEAD requests only need Content-Length — skip expensive moov relocation
        if ext in ('.mp4', '.m4v', '.mov') and self.command != 'HEAD':
            mp4_data = _get_mp4_content(filepath)

        try:
            if mp4_data is not None:
                file_size = len(mp4_data)
                range_header = self.headers.get('Range', '')
                if range_header:
                    parts = range_header.strip().replace('bytes=', '').split('-')
                    start = int(parts[0]) if parts[0] else 0
                    end   = int(parts[1]) if len(parts) > 1 and parts[1] else file_size - 1
                    end   = min(end, file_size - 1)
                    length = end - start + 1
                    self.send_response(206)
                    self.send_header('Content-Type', mime)
                    self.send_header('Content-Range', f'bytes {start}-{end}/{file_size}')
                    self.send_header('Content-Length', str(length))
                    self.send_header('Accept-Ranges', 'bytes')
                    self.send_header('Cache-Control', 'no-cache')
                    self.send_header('Access-Control-Allow-Origin', '*')
                    self.end_headers()
                    if self.command != 'HEAD':
                        try:
                            self.wfile.write(mp4_data[start: end + 1])
                        except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
                            pass
                else:
                    self.send_response(200)
                    self.send_header('Content-Type', mime)
                    self.send_header('Content-Length', str(file_size))
                    self.send_header('Accept-Ranges', 'bytes')
                    self.send_header('Cache-Control', 'no-cache')
                    self.send_header('Access-Control-Allow-Origin', '*')
                    self.end_headers()
                    if self.command != 'HEAD':
                        try:
                            pos = 0
                            while pos < file_size:
                                self.wfile.write(mp4_data[pos: pos + CHUNK])
                                pos += CHUNK
                        except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
                            pass
            else:
                file_size = os.path.getsize(filepath)
                range_header = self.headers.get('Range', '')
                if range_header:
                    parts = range_header.strip().replace('bytes=', '').split('-')
                    start = int(parts[0]) if parts[0] else 0
                    end   = int(parts[1]) if len(parts) > 1 and parts[1] else file_size - 1
                    end   = min(end, file_size - 1)
                    length = end - start + 1
                    self.send_response(206)
                    self.send_header('Content-Type', mime)
                    self.send_header('Content-Range', f'bytes {start}-{end}/{file_size}')
                    self.send_header('Content-Length', str(length))
                    self.send_header('Accept-Ranges', 'bytes')
                    self.send_header('Cache-Control', 'no-cache')
                    self.send_header('Access-Control-Allow-Origin', '*')
                    self.end_headers()
                    if self.command != 'HEAD':
                        try:
                            with open(filepath, 'rb') as f:
                                f.seek(start)
                                remaining = length
                                while remaining > 0:
                                    chunk = f.read(min(CHUNK, remaining))
                                    if not chunk:
                                        break
                                    self.wfile.write(chunk)
                                    remaining -= len(chunk)
                        except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
                            pass
                else:
                    self.send_response(200)
                    self.send_header('Content-Type', mime)
                    self.send_header('Content-Length', str(file_size))
                    self.send_header('Accept-Ranges', 'bytes')
                    self.send_header('Cache-Control', 'no-cache')
                    self.send_header('Access-Control-Allow-Origin', '*')
                    self.end_headers()
                    if self.command != 'HEAD':
                        try:
                            with open(filepath, 'rb') as f:
                                while True:
                                    chunk = f.read(CHUNK)
                                    if not chunk:
                                        break
                                    self.wfile.write(chunk)
                        except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
                            pass
        except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
            pass  # browser cancelled the request (e.g. user switched video)
        except (PermissionError, OSError):
            self.send_error(403)

    # ─── Proxy handler ─────────────────────────────────────────────────────
    def _proxy(self, target_host=TARGET_HOST, prefix=PROXY_PATH_PREFIX):
        remote_path = self.path[len(prefix):]
        if not remote_path or remote_path == '/':
            remote_path = '/'
        target_url = f'https://{target_host}{remote_path}'

        headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
            'Accept': self.headers.get('Accept', '*/*'),
            'Accept-Language': 'en-US,en;q=0.9',
            'Accept-Encoding': 'identity',
            'Referer': f'https://{TARGET_HOST}/',
        }
        if self.headers.get('Cookie'):
            headers['Cookie'] = self.headers['Cookie']

        body_data = None
        if self.command in ('POST', 'PUT', 'PATCH'):
            content_length = int(self.headers.get('Content-Length', 0))
            if content_length > 0:
                body_data = self.rfile.read(content_length)
            if self.headers.get('Content-Type'):
                headers['Content-Type'] = self.headers['Content-Type']

        req = urllib.request.Request(target_url, data=body_data, headers=headers, method=self.command)
        try:
            with urllib.request.urlopen(req, context=SSL_CTX, timeout=30) as resp:
                body = resp.read()
                content_type = resp.headers.get('Content-Type', '').lower()
                status = resp.status

                out_headers = {}
                for k, v in resp.headers.items():
                    if k.lower() not in STRIP_HEADERS:
                        out_headers[k] = v

                if 'text/html' in content_type or 'text/css' in content_type:
                    text = body.decode('utf-8', errors='replace')
                    text = text.replace(f'https://{CDN_HOST}/', f'{CDN_PROXY_PATH_PREFIX}/')
                    if 'text/html' in content_type:
                        text = re.sub(
                            r'\s+crossorigin(?:=["\'][^"\']*["\'])?',
                            '',
                            text,
                            flags=re.IGNORECASE
                        )
                        if '</head>' in text:
                            text = text.replace('</head>', AUTO_LOGIN_JS + '</head>', 1)
                        elif '<body' in text:
                            text = text.replace('<body', AUTO_LOGIN_JS + '<body', 1)
                    body = text.encode('utf-8')
                    out_headers['Content-Length'] = str(len(body))

                self.send_response(status)
                for k, v in out_headers.items():
                    if k.lower() == 'content-length' and ('text/html' in content_type or 'text/css' in content_type):
                        continue
                    self.send_header(k, v)
                self.send_header('Access-Control-Allow-Origin', '*')
                self.end_headers()
                if self.command != 'HEAD':
                    self.wfile.write(body)

        except urllib.error.HTTPError as e:
            self.send_error(e.code, str(e.reason))
        except Exception as e:
            self.send_error(502, str(e))

    # ─── Full-text search (SRT cues + text files) ────────────────────────────
    def _check_version(self):
        import urllib.request as _urlreq
        try:
            req = _urlreq.Request(
                'https://api.github.com/repos/patchamama/Course-viewer/releases/latest',
                headers={'User-Agent': 'CourseViewer/1.0'}
            )
            with _urlreq.urlopen(req, timeout=5) as r:
                data = json.loads(r.read())
            latest = data.get('tag_name', '').lstrip('v')
            html_path = os.path.join(APP_DIR, 'course-viewer.html')
            local = '0'
            if os.path.isfile(html_path):
                with open(html_path, encoding='utf-8', errors='ignore') as f:
                    m = re.search(r"APP_VERSION\s*=\s*'([0-9][0-9.]*)'", f.read())
                    if m: local = m.group(1)
            def _ver_gt(a, b):
                av = [int(x) for x in a.split('.')]
                bv = [int(x) for x in b.split('.')]
                return av > bv
            has_update = bool(latest) and _ver_gt(latest, local)
            self._json({'hasUpdate': has_update, 'latestVersion': latest, 'currentVersion': local})
        except Exception as e:
            self._json({'hasUpdate': False, 'latestVersion': '', 'currentVersion': '', 'error': str(e)})

    def _self_update(self):
        import urllib.request as _urlreq
        GITHUB_RAW = 'https://raw.githubusercontent.com/patchamama/Course-viewer/main'
        updated, errors = [], []
        for fname in ['proxy.py', 'course-viewer.html']:
            target = os.path.join(APP_DIR, fname)
            try:
                req = _urlreq.Request(f'{GITHUB_RAW}/{fname}',
                                      headers={'User-Agent': 'CourseViewer/1.0'})
                with _urlreq.urlopen(req, timeout=30) as r:
                    content = r.read()
                with open(target, 'wb') as f:
                    f.write(content)
                updated.append(fname)
            except Exception as e:
                errors.append(f'{fname}: {str(e)}')
        self._json({'updated': updated, 'errors': errors, 'ok': len(errors) == 0})

    def _search(self):
        params = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
        q = params.get('q', [''])[0].strip().lower()
        if not q:
            self._json({'query': q, 'results': []}); return
        path_filter = params.get('path', [''])[0].strip('/')
        if path_filter:
            scan_root = os.path.normpath(os.path.join(STATIC_DIR, path_filter))
            if not scan_root.startswith(os.path.normpath(STATIC_DIR)):
                scan_root = STATIC_DIR
        else:
            scan_root = STATIC_DIR
        try:
            max_depth = max(0, min(10, int(params.get('depth', ['3'])[0])))
        except (ValueError, TypeError):
            max_depth = 3
        SRT_EXTS  = {'.srt', '.vtt'}
        TEXT_EXTS = {'.txt', '.md', '.html', '.htm', '.js', '.json', '.xml', '.php', '.py'}
        ALL_EXTS  = SRT_EXTS | TEXT_EXTS
        MAX_FILE  = 3 * 1024 * 1024
        results   = []
        scan_root_depth = scan_root.rstrip(os.sep).count(os.sep)
        for root, dirs, files in os.walk(scan_root):
            cur_depth = root.rstrip(os.sep).count(os.sep) - scan_root_depth
            if cur_depth >= max_depth:
                dirs[:] = []  # don't recurse deeper
            else:
                dirs[:] = sorted(
                    [d for d in dirs if not d.startswith('.') and not d.startswith('_')],
                    key=str.lower
                )
            # Folder name matches
            for dname in dirs:
                if q in dname.lower():
                    rel = os.path.relpath(os.path.join(root, dname), STATIC_DIR).replace('\\', '/')
                    results.append({'file': rel, 'type': 'folder', 'matches': [{'text': dname}]})
                    if len(results) >= 50: break
            for fname in sorted(files, key=str.lower):
                if fname.startswith('.') or fname.startswith('_'):
                    continue
                ext = os.path.splitext(fname)[1].lower()
                fpath = os.path.join(root, fname)
                rel_name = os.path.relpath(fpath, STATIC_DIR).replace('\\', '/')
                # Filename match (all file types) — prepend so name hits appear first
                if q in fname.lower() and not any(r['file'] == rel_name and r['type'] == 'filename' for r in results):
                    results.append({'file': rel_name, 'type': 'filename', 'matches': [{'text': fname}]})
                    if len(results) >= 50: break
                if ext not in ALL_EXTS:
                    continue
                try:
                    if os.path.getsize(fpath) > MAX_FILE:
                        continue
                    with open(fpath, encoding='utf-8', errors='ignore') as f:
                        content = f.read()
                except Exception:
                    continue
                rel = os.path.relpath(fpath, STATIC_DIR).replace('\\', '/')
                if ext in SRT_EXTS:
                    matches = []
                    for block in re.split(r'\n\s*\n', content):
                        lines = block.strip().splitlines()
                        time_line, text_start = None, 0
                        for i, line in enumerate(lines):
                            if '-->' in line:
                                time_line = line; text_start = i + 1; break
                        if not time_line:
                            continue
                        cue_text = ' '.join(lines[text_start:]).strip()
                        if q not in cue_text.lower():
                            continue
                        tm = re.match(r'(\d{1,2}):(\d{2}):(\d{2})[,.]', time_line)
                        secs = (int(tm.group(1))*3600 + int(tm.group(2))*60 + int(tm.group(3))) if tm else 0
                        matches.append({'time': secs, 'text': cue_text[:300]})
                        if len(matches) >= 30:
                            break
                    if matches:
                        results.append({'file': rel, 'type': 'srt', 'matches': matches})
                else:
                    matches = []
                    for i, line in enumerate(content.splitlines()):
                        if q in line.lower():
                            matches.append({'line': i + 1, 'text': line.strip()[:300]})
                            if len(matches) >= 20:
                                break
                    if matches:
                        results.append({'file': rel, 'type': ext.lstrip('.'), 'matches': matches})
                if len(results) >= 50:
                    break
        self._json({'query': q, 'results': results})

    # ─── Raw file content for code viewer ────────────────────────────────────
    def _file_content(self):
        params = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
        rel = urllib.parse.unquote(params.get('path', [''])[0]).strip('/')
        if not rel:
            self.send_error(400, 'Missing path'); return
        absp = os.path.normpath(os.path.join(STATIC_DIR, rel))
        if not absp.startswith(os.path.normpath(STATIC_DIR) + os.sep) and absp != os.path.normpath(STATIC_DIR):
            self.send_error(403, 'Access denied'); return
        if not os.path.isfile(absp):
            self.send_error(404, 'Not found'); return
        if os.path.getsize(absp) > 2 * 1024 * 1024:
            self.send_error(413, 'File too large'); return
        try:
            with open(absp, encoding='utf-8', errors='replace') as f:
                content = f.read()
            self._json({'path': rel, 'content': content})
        except Exception as e:
            self.send_error(500, str(e))

    def log_message(self, fmt, *args):
        if args and (str(args[1]).startswith('5') or '/proxy/' in str(args[0])):
            sys.stderr.write(f'[proxy] {self.address_string()} {fmt % args}\n')


class _ThreadingServer(socketserver.ThreadingMixIn, http.server.HTTPServer):
    daemon_threads = True
    allow_reuse_address = True


def run():
    server = _ThreadingServer(('', PORT), Handler)
    print(f'  Course Viewer running at  http://localhost:{PORT}/')
    print(f'  Course proxy at         http://localhost:{PORT}/proxy/')
    print(f'  Press Ctrl+C to stop.')
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print('\nStopped.')


if __name__ == '__main__':
    run()
