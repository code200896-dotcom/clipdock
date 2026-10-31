# ClipDock

A private, password-protected video downloader website. Paste a public video
link, choose a resolution limit, inspect details, download, and save the file.
The responsive interface works on phones and computers.

## Deployment

Prepared for a single password-protected Render Docker web service. The included
Blueprint selects free compute for initial testing. Free services can sleep,
restart, and lose temporary files; bandwidth and build usage quotas still apply.
No passwords are stored in this repository. Set DOWNLOAD_PASSWORD in Render.

## Validation completed

- Nine automated tests cover authentication, static delivery, invalid inputs,
  cross-site submission rejection, job failure/success handling, capacity
  release, protected file delivery, private IP blocking, and mixed DNS answers.
- A tenth integration test uses a locally generated one-second video with tone
  audio. Real yt-dlp extracts and downloads it. A second stage downloads separate
  video/audio tracks and merges them with real FFmpeg. FFprobe confirms both
  streams in the output. Local network guards are disabled only inside that
  fixture test so it can reach its own loopback server.
- Python application import and JavaScript syntax checks passed.
- Public website testing could not complete because this execution environment
  could not resolve the external sample-video hostname.
- Docker image build, browser layout inspection, hosted deployment, and real
  social-site extraction remain unverified. Test intended sites after deployment.

The tested versions are yt-dlp 2026.10.26, FastAPI 0.162.0, and Uvicorn 0.54.0.
Python 3.12, Node 24, and system FFmpeg were available for local checks. The
Dockerfile installs Python, FFmpeg, Node 22, and the pinned application packages.

## Deploy on Render

1. Create a private GitHub, GitLab, or Bitbucket repository containing this
   folder's contents at its root. Keep passwords outside the repository.
2. Select your intended Render workspace.
3. Either create a Docker Web Service from that repository or create a Blueprint
   using the included `render.yaml`. The configuration follows Render's documented Blueprint fields; CLI/API
   Blueprint validation has not been performed.
4. Review the selected compute plan and current prices before creating it.
   Use one instance. Job state and files are deliberately temporary.
5. Set `DOWNLOAD_PASSWORD` to a unique random value of at least 16 characters.
   The Blueprint requests a generated value. Retrieve it privately from the
   service's environment settings. The sign-in username is **owner**.
6. The Docker start command binds to `0.0.0.0:$PORT`. Configure the health check
   as `/health`. No database, Redis, or persistent disk is required.
7. Open the assigned HTTPS URL and enter the credentials in the browser prompt.
   Test a short video from each intended source and confirm audio playback.

Native browser authentication has no reliable logout button. Close the private
browsing session when using a shared computer. Use HTTPS for a hosted deployment.

Official references:

- https://render.com/docs/docker
- https://render.com/docs/blueprint-spec
- https://render.com/docs/web-services
- https://github.com/yt-dlp/yt-dlp

## Local test run

Requires Python 3.10+, a supported Node runtime, and FFmpeg on PATH:

```bash
python -m venv .venv
. .venv/bin/activate
pip install -r requirements.txt
export DOWNLOAD_PASSWORD='choose-a-unique-long-random-password'
uvicorn app:app --host 127.0.0.1 --port 8080 --no-access-log
```

Open http://localhost:8080. On Windows, activate the environment with
`.venv\Scripts\Activate.ps1` and set the password using
`$env:DOWNLOAD_PASSWORD = Read-Host 'Password (16+ characters)'`.

Docker alternative, with the environment variable already set:

```bash
docker build -t clipdock .
docker run --rm -p 127.0.0.1:8080:8080 -e DOWNLOAD_PASSWORD clipdock
```

For tests, install `httpx` and run `python -m unittest -v` from this directory.
The integration test requires FFmpeg/FFprobe and the libx264/AAC encoders.

## Supported use and limits

- Supports individual public video links handled by the installed yt-dlp
  version, including many social/video sites and direct media URLs. Every
  website is not guaranteed. Source sites can change or block datacenter IPs.
- No account cookies, paywall bypass, DRM removal, live broadcasts, or playlists.
  Save videos you own or have permission to download.
- Resolution limits: 360p, 480p, 720p, 1080p, and 4K. Files whose height is unknown
  use their source version. Resolution is capped when reported, never upscaled.
- Source-compatible MP4, MKV, or WebM output. MP4 is preferred when compatible;
  no expensive transcoding or guaranteed conversion to MP4 is included.
- Single active inspection/download. Processing timeout: 30 minutes. Maximum
  reported video duration: two hours. Final file limit: 1 GB. Unknown sizes,
  separate tracks, and merging can temporarily require more disk space.
- Up to five recent job records, with the oldest finished job removed for a new
  one. Other finished files expire after one hour. Save files before starting
  more downloads. Server restarts discard all job state and temporary files.
- This is one password-protected private workspace, not a multi-user product.
  All signed-in users share its jobs and files. Do not make it anonymous/public.
- Page refresh reconnects to an active job in the same tab when the server is
  still running. Job resumes after server restarts are not supported.

## Deployment boundaries

Keep the generated password secret and deploy on an isolated container host.
The web app requires authentication on all routes except the basic health check.
Paths are generated by the server; supplied URLs never become output filenames.
Downloader subprocesses have absolute timeouts and are killed as a process group.

The Python worker blocks private/local addresses at socket connection time and
connects to checked numeric addresses. Only ordinary HTTP(S), native HLS, and
HTTP DASH formats are selected. These are defense-in-depth controls, not an OS
network sandbox. External processes are outside Python's socket hooks. A public
multi-user launch needs container-level egress controls, rate limits, stronger
user isolation, and additional operational testing first.

Rebuild after updating yt-dlp when source sites change. Pin and verify each new
version rather than silently updating the running service.
