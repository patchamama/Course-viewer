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

PORT = int(os.environ.get('OC_PORT', '8080'))
STATIC_DIR = os.path.dirname(os.path.abspath(__file__))
TARGET_HOST = 'share.articulate.com'
PROXY_PATH_PREFIX = '/proxy'
CDN_HOST = 'cdn.articulate.com'
CDN_PROXY_PATH_PREFIX = '/proxy-cdn'

# Read password from config.json
_password = 'Handout4EFB'
try:
    with open(os.path.join(STATIC_DIR, 'config.json'), 'r', encoding='utf-8') as f:
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
        elif self.path == '/list-local-files':
            self._list_local_files()
        elif self.path.startswith('/local-video'):
            self._serve_local_video()
        else:
            self._static()

    def do_HEAD(self):
        self.do_GET()

    def do_POST(self):
        if self.path == '/save-config':
            self._handle_save_config()
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

    # ─── List local docs and images for auto-open in Course Reader ───────────
    def _list_local_files(self):
        DOC_EXTS = {'.pdf', '.md', '.html', '.htm', '.epub', '.txt'}
        IMG_EXTS = {'.png', '.jpg', '.jpeg', '.gif', '.svg', '.webp'}
        EXCLUDE_NAMES = {
            'index.html', 'open_course.html', 'course.readme.txt',
            'config.json', 'config.json.example', 'readme.md'
        }
        EXCLUDE_EXTS = {'.py', '.sh', '.bat', '.gitignore', '.json'}
        docs = []
        images = []
        try:
            for fname in sorted(os.listdir(STATIC_DIR)):
                if fname.startswith('.') or fname.startswith('_'):
                    continue
                if fname.lower() in EXCLUDE_NAMES:
                    continue
                fpath = os.path.join(STATIC_DIR, fname)
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
            config_path = os.path.join(STATIC_DIR, 'config.json')
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
    def _static(self):
        raw = self.path.split('?')[0].split('#')[0]
        raw = urllib.parse.unquote(raw)
        if raw in ('', '/'):
            raw = '/index.html'
        filepath = os.path.normpath(os.path.join(STATIC_DIR, raw.lstrip('/')))
        if not filepath.startswith(STATIC_DIR):
            self.send_error(403)
            return
        if os.path.isdir(filepath):
            filepath = os.path.join(filepath, 'index.html')
        if not os.path.isfile(filepath):
            self.send_error(404, f'Not found: {raw}')
            return

        ext = os.path.splitext(filepath)[1].lower()
        mime = MIME_MAP.get(ext, mimetypes.guess_type(filepath)[0] or 'application/octet-stream')

        # Inject _dirId into config.json
        if os.path.basename(filepath) == 'config.json' and os.path.dirname(filepath) == STATIC_DIR:
            try:
                with open(filepath, 'r', encoding='utf-8') as f:
                    cfg = json.load(f)
                cfg['_dirId'] = hashlib.md5(STATIC_DIR.encode()).hexdigest()[:8]
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
            except Exception:
                pass  # fall through to normal file serving

        self._send_file(filepath, mime)

    # ─── Shared file sender (Range + moov relocation) ─────────────────────────
    def _send_file(self, filepath: str, mime: str):
        CHUNK = 512 * 1024

        ext = os.path.splitext(filepath)[1].lower()
        mp4_data = None
        if ext in ('.mp4', '.m4v', '.mov'):
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
                        except (BrokenPipeError, ConnectionResetError):
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
                        except (BrokenPipeError, ConnectionResetError):
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
                        except (BrokenPipeError, ConnectionResetError):
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
                        except (BrokenPipeError, ConnectionResetError):
                            pass
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
