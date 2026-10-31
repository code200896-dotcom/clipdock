import asyncio
import contextlib
import hashlib
import json
import os
import secrets
import shutil
import signal
import sys
import time
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import Depends, FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.security import HTTPBasic, HTTPBasicCredentials
from pydantic import BaseModel, Field
from worker import public_url

ROOT = Path(__file__).resolve().parent
DATA = Path(os.getenv('DATA_DIR', '/tmp/clipdock'))
PASSWORD = os.getenv('DOWNLOAD_PASSWORD', '')
MAX_BYTES = 1024**3
TTL = 3600
jobs = {}
busy = False
security = HTTPBasic()


def owner(credentials: HTTPBasicCredentials = Depends(security)):
    valid = secrets.compare_digest(hashlib.sha256(credentials.password.encode()).digest(), hashlib.sha256(PASSWORD.encode()).digest())
    if not PASSWORD or credentials.username != 'owner' or not valid:
        raise HTTPException(401, 'Incorrect username or password.', headers={'WWW-Authenticate': 'Basic realm="ClipDock"'})


async def cleanup():
    while True:
        await asyncio.sleep(60)
        for key, job in list(jobs.items()):
            if job.get('finished', time.time()) < time.time() - TTL:
                shutil.rmtree(DATA / key, ignore_errors=True)
                jobs.pop(key, None)


@asynccontextmanager
async def lifespan(app):
    if len(PASSWORD) < 16:
        raise RuntimeError('Set DOWNLOAD_PASSWORD to a unique random password of at least 16 characters.')
    DATA.mkdir(parents=True, exist_ok=True)
    for old in DATA.iterdir():
        if old.is_dir() and old.name.startswith('job-'):
            shutil.rmtree(old)
    task = asyncio.create_task(cleanup())
    yield
    task.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await task
    running = list(tasks)
    for pending in running:
        pending.cancel()
    await asyncio.gather(*running, return_exceptions=True)


app = FastAPI(lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)


@app.middleware('http')
async def protect(request, call_next):
    if request.method == 'POST' and (request.headers.get('sec-fetch-site') == 'cross-site' or request.headers.get('content-type', '').split(';')[0] != 'application/json'):
        from fastapi.responses import JSONResponse
        return JSONResponse({'detail': 'Use the downloader page to submit a link.'}, status_code=403)
    try:
        length = int(request.headers.get('content-length', '0') or '0')
    except ValueError:
        length = 8193
    if length < 0 or length > 8192:
        from fastapi.responses import JSONResponse
        return JSONResponse({'detail': 'Request is too large.'}, status_code=413)
    response = await call_next(request)
    response.headers.update({'Cache-Control': 'no-store', 'X-Content-Type-Options': 'nosniff',
                             'Referrer-Policy': 'no-referrer',
                             'Content-Security-Policy': "default-src 'self'; script-src 'self'; style-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'"})
    return response


class Video(BaseModel):
    url: str = Field(min_length=8, max_length=4096)
    height: int = 1080

    def checked(self):
        try:
            public_url(self.url)
            if self.height not in (360, 480, 720, 1080, 2160):
                raise ValueError('Choose one of the offered quality limits.')
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc
        return self


def stop(process):
    if process.returncode is None:
        with contextlib.suppress(ProcessLookupError):
            os.killpg(process.pid, signal.SIGKILL)


async def execute(spec, callback=None, timeout=90):
    process = await asyncio.create_subprocess_exec(sys.executable, str(ROOT / 'worker.py'),
        stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL,
        start_new_session=True)
    last = None
    try:
        async with asyncio.timeout(timeout):
            process.stdin.write(json.dumps(spec).encode())
            await process.stdin.drain()
            process.stdin.close()
            async for line in process.stdout:
                last = json.loads(line)
                if callback:
                    callback(last)
            await process.wait()
        if process.returncode or not last:
            raise ValueError(last.get('message', 'The downloader stopped.') if last else 'The downloader stopped.')
        return last
    except TimeoutError as exc:
        raise ValueError('The source took too long. Try a shorter video or another link.') from exc
    finally:
        stop(process)
        await process.wait()


async def download(key, video):
    global busy
    directory = DATA / key
    try:
        directory.mkdir(parents=True)
        def update(event):
            jobs[key].update({k: event[k] for k in ('percent', 'message') if k in event})
            if sum(p.stat().st_size for p in directory.glob('*') if p.is_file()) > MAX_BYTES * 2:
                raise ValueError('Temporary download storage exceeded the limit.')
        await execute({'mode': 'download', 'url': video.url, 'height': video.height, 'directory': str(directory)}, update, 1800)
        files = [p for p in directory.iterdir() if p.suffix.lower() in ('.mp4', '.mkv', '.webm', '.mov')]
        if len(files) != 1 or files[0].stat().st_size > MAX_BYTES:
            raise ValueError('No single supported video file within the 1 GB limit was produced.')
        jobs[key].update(status='ready', percent=100, message='Your video is ready.', file=str(files[0]), finished=time.time())
    except asyncio.CancelledError:
        shutil.rmtree(directory, ignore_errors=True)
        raise
    except Exception as exc:
        shutil.rmtree(directory, ignore_errors=True)
        jobs[key].update(status='error', message=str(exc), finished=time.time())
    finally:
        busy = False


@app.get('/health')
async def health(): return {'status': 'ok'}


@app.get('/', dependencies=[Depends(owner)])
async def index(): return FileResponse(ROOT / 'web/index.html')


@app.get('/app.js', dependencies=[Depends(owner)])
async def script(): return FileResponse(ROOT / 'web/app.js', media_type='application/javascript')


@app.get('/style.css', dependencies=[Depends(owner)])
async def style(): return FileResponse(ROOT / 'web/style.css', media_type='text/css')


@app.post('/api/inspect', dependencies=[Depends(owner)])
async def inspect(video: Video):
    global busy
    video.checked()
    if busy: raise HTTPException(409, 'Another video is processing. Please wait.')
    busy = True
    try:
        return await execute({'mode': 'inspect', 'url': video.url})
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    finally:
        busy = False


tasks = set()


@app.post('/api/jobs', status_code=202, dependencies=[Depends(owner)])
async def start(video: Video):
    global busy
    video.checked()
    if busy: raise HTTPException(409, 'Another video is processing. Please wait.')
    busy = True
    if len(jobs) >= 5:
        finished = [k for k, v in jobs.items() if v['status'] != 'working']
        if finished:
            old = min(finished, key=lambda k: jobs[k]['created'])
            shutil.rmtree(DATA / old, ignore_errors=True)
            del jobs[old]
    key = 'job-' + secrets.token_urlsafe(24)
    jobs[key] = dict(id=key, status='working', percent=None, message='Connecting to the website…', created=time.time())
    task = asyncio.create_task(download(key, video))
    tasks.add(task)
    task.add_done_callback(tasks.discard)
    return {'id': key}


@app.get('/api/jobs/{key}', dependencies=[Depends(owner)])
async def state(key: str):
    if key not in jobs: raise HTTPException(404, 'This download expired or the server restarted.')
    return {k: v for k, v in jobs[key].items() if k != 'file'}


@app.get('/files/{key}', dependencies=[Depends(owner)])
async def file(key: str):
    job = jobs.get(key, {})
    path = Path(job['file']) if job.get('status') == 'ready' else None
    if not path or not path.is_file(): raise HTTPException(404, 'The file expired. Please download it again.')
    return FileResponse(path, filename='clipdock-video' + path.suffix, media_type='application/octet-stream')
