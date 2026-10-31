"""Isolated extraction process; JSON events are the only stdout output."""
import ipaddress
import json
import socket
import sys
from urllib.parse import urlsplit

ALLOWED_PROTOCOLS = {'http', 'https', 'm3u8_native', 'http_dash_segments'}


def public_url(url):
    p = urlsplit(url)
    if p.scheme not in ('http', 'https') or not p.hostname or p.username or p.password or p.port not in (None, 80, 443):
        raise ValueError('Use a public HTTP or HTTPS video link without login details or custom ports.')
    return url


def public_address(address):
    host = address[0]
    try:
        ip = ipaddress.ip_address(host)
        candidates = [address]
    except ValueError:
        candidates = [r[4] for r in socket.getaddrinfo(host, address[1], type=socket.SOCK_STREAM)]
    if not candidates or any(not ipaddress.ip_address(a[0]).is_global for a in candidates):
        raise ValueError('Connections to private or local networks are blocked.')
    return candidates


def protect_network():
    original_connect = socket.socket.connect
    original_connect_ex = socket.socket.connect_ex

    def checked(original):
        def connect(self, address):
            if self.family not in (socket.AF_INET, socket.AF_INET6):
                raise ValueError('Only public internet connections are supported.')
            candidates = public_address(address)
            compatible = [a for a in candidates if (':' in a[0]) == (self.family == socket.AF_INET6)]
            if not compatible:
                raise OSError('No compatible public network address.')
            # Connect to the checked numeric address, avoiding another DNS lookup.
            return original(self, compatible[0])
        return connect

    socket.socket.connect = checked(original_connect)
    socket.socket.connect_ex = checked(original_connect_ex)


def emit(event):
    print(json.dumps(event), flush=True)


class Quiet:
    def debug(self, *_): pass
    def warning(self, *_): pass
    def error(self, *_): pass


def run(spec):
    from yt_dlp import YoutubeDL
    public_url(spec['url'])
    protect_network()

    def check(info, *, incomplete=False):
        if info.get('_type') in ('playlist', 'multi_video'):
            return 'Use a single video URL instead of a playlist.'
        if info.get('is_live'):
            return 'Live broadcasts are not supported.'
        if (info.get('duration') or 0) > 7200:
            return 'Choose a video shorter than two hours.'

    opts = dict(quiet=True, no_warnings=True, logger=Quiet(), noplaylist=True,
                playlistend=1, cachedir=False, socket_timeout=20, retries=2,
                fragment_retries=2, proxy='', js_runtimes={'node': {}},
                hls_prefer_native=True, max_filesize=1024**3, match_filter=check,
                enable_file_urls=False)

    def progress(p):
        total = p.get('total_bytes') or p.get('total_bytes_estimate')
        emit({'kind': 'progress', 'percent': round(100 * p.get('downloaded_bytes', 0) / total, 1) if total and p['status'] == 'downloading' else None,
              'message': 'Downloading video and audio…' if p['status'] == 'downloading' else 'Preparing your file…'})

    if spec['mode'] != 'inspect':
        height = int(spec['height'])
        opts.update({'format': f'bv*[height<=?{height}]+ba/b[height<=?{height}]',
                     'outtmpl': spec['directory'] + '/video.%(ext)s',
                     'merge_output_format': 'mp4/mkv', 'progress_hooks': [progress]})

    with YoutubeDL(opts) as ydl:
        info = ydl.extract_info(spec['url'], download=False)
        reason = check(info)
        if reason:
            raise ValueError(reason)
        formats = [f for f in info.get('formats', []) if f.get('protocol') in ALLOWED_PROTOCOLS and not f.get('has_drm')]
        if not formats:
            raise ValueError('This source has no supported, unprotected video download.')
        info['formats'] = formats
        if spec['mode'] == 'inspect':
            emit({'kind': 'info', 'title': info.get('title', 'Video'), 'duration': info.get('duration'),
                  'source': info.get('extractor_key', 'Website'),
                  'heights': sorted({f['height'] for f in formats if f.get('height') and f.get('vcodec') != 'none'})})
            return
        # Re-process the sanitized format list so external/unsupported protocols
        # cannot be selected by a fallback extractor or manifest.
        ydl.process_ie_result(info, download=True)
        emit({'kind': 'done', 'title': info.get('title', 'Video')})


if __name__ == '__main__':
    try:
        run(json.loads(sys.stdin.read()))
    except Exception as exc:
        emit({'kind': 'error', 'message': str(exc)[:600]})
        sys.exit(1)
