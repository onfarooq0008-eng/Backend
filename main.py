import os
import shutil
import subprocess
import tempfile
import threading
import time
from collections import defaultdict, deque
from glob import glob

import yt_dlp
from fastapi import Depends, FastAPI, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from pydantic import BaseModel
from starlette.background import BackgroundTask

RATE_LIMIT = int(os.getenv("RATE_LIMIT_PER_MIN", "30"))
MAX_PARALLEL = int(os.getenv("MAX_PARALLEL_DOWNLOADS", "2"))
MAX_FILESIZE_MB = int(os.getenv("MAX_FILESIZE_MB", "500"))
HD_HEIGHT = int(os.getenv("HD_MAX_HEIGHT", "1080"))
SD_HEIGHT = int(os.getenv("SD_MAX_HEIGHT", "480"))

# ---- Cookies (needed for Instagram etc.) ---------------------------------
# Provide ONE of: cookies.txt next to main.py, COOKIES_FILE=<path>,
# or COOKIES_TXT=<full Netscape cookies text> as an environment variable.
# The file is copied to /tmp because yt-dlp writes back to it.
COOKIE_PATH = None


def _setup_cookies():
    global COOKIE_PATH
    dest = os.path.join(tempfile.gettempdir(), "cookies.txt")
    src = os.getenv("COOKIES_FILE") or os.path.join(os.path.dirname(__file__), "cookies.txt")
    text = os.getenv("COOKIES_TXT")
    if text:
        with open(dest, "w") as f:
            f.write(text.replace("\\n", "\n") if "\n" not in text else text)
        COOKIE_PATH = dest
    elif os.path.exists(src):
        shutil.copyfile(src, dest)
        COOKIE_PATH = dest


_setup_cookies()

OPTIONS = [
    {"id": "hd", "label": "HD", "type": "video", "max_height": HD_HEIGHT},
    {"id": "sd", "label": "SD", "type": "video", "max_height": SD_HEIGHT},
    {"id": "audio", "label": "Audio (MP3)", "type": "audio"},
]

app = FastAPI(title="Media Downloader API", version="2.0.0")
app.add_middleware(
    CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"]
)

_slots = threading.Semaphore(MAX_PARALLEL)
_hits: dict[str, deque] = defaultdict(deque)


def guard(request: Request):
    ip = request.headers.get("x-forwarded-for", request.client.host).split(",")[0].strip()
    now = time.time()
    q = _hits[ip]
    while q and now - q[0] > 60:
        q.popleft()
    if len(q) >= RATE_LIMIT:
        raise HTTPException(429, "Too many requests, slow down")
    q.append(now)


class UrlBody(BaseModel):
    url: str


def base_opts(**extra):
    opts = {
        "quiet": True,
        "no_warnings": True,
        "noplaylist": True,
        "socket_timeout": 20,
        "retries": 3,
        "geo_bypass": True,
    }
    if COOKIE_PATH:
        opts["cookiefile"] = COOKIE_PATH
    opts.update(extra)
    return opts


def extract(url: str):
    try:
        with yt_dlp.YoutubeDL(base_opts()) as ydl:
            return ydl.extract_info(url, download=False)
    except yt_dlp.utils.DownloadError as e:
        raise HTTPException(422, str(e).replace("ERROR: ", "")[:300])


def summarize(info: dict) -> dict:
    heights = sorted(
        {f["height"] for f in info.get("formats", []) if f.get("height")}, reverse=True
    )
    return {
        "id": info.get("id"),
        "title": info.get("title"),
        "uploader": info.get("uploader"),
        "duration": info.get("duration"),
        "thumbnail": info.get("thumbnail"),
        "extractor": info.get("extractor_key"),
        "available_heights": heights,
        "options": OPTIONS,
    }


@app.get("/")
@app.get("/health")
def health():
    return {
        "status": "ok",
        "yt_dlp": yt_dlp.version.__version__,
        "ffmpeg": shutil.which("ffmpeg") is not None,
        "cookies_loaded": COOKIE_PATH is not None,
    }


@app.get("/api/info", dependencies=[Depends(guard)])
def info_get(url: str):
    return summarize(extract(url))


@app.post("/api/info", dependencies=[Depends(guard)])
def info_post(body: UrlBody):
    return summarize(extract(body.url))


def has_audio(path: str) -> bool:
    try:
        out = subprocess.run(
            ["ffprobe", "-v", "error", "-select_streams", "a",
             "-show_entries", "stream=codec_type", "-of", "csv=p=0", path],
            capture_output=True, text=True, timeout=30,
        ).stdout
        return "audio" in out
    except Exception:
        return True  # cannot check, assume fine


@app.get("/api/formats", dependencies=[Depends(guard)])
def formats(url: str):
    """Debug: lists every format yt-dlp sees (codecs tell if a stream has audio)."""
    info = extract(url)
    return [
        {
            "format_id": f.get("format_id"),
            "ext": f.get("ext"),
            "width": f.get("width"),
            "height": f.get("height"),
            "vcodec": f.get("vcodec"),
            "acodec": f.get("acodec"),
            "abr": f.get("abr"),
            "protocol": f.get("protocol"),
        }
        for f in info.get("formats", [])
    ]


def run_download(url: str, folder: str, opts: dict) -> str:
    os.makedirs(folder, exist_ok=True)
    for f in glob(os.path.join(folder, "*")):
        os.remove(f)
    opts = dict(opts, outtmpl=os.path.join(folder, "%(title).80B.%(ext)s"))
    try:
        with yt_dlp.YoutubeDL(opts) as ydl:
            ydl.extract_info(url, download=True)
    except yt_dlp.utils.DownloadError as e:
        raise HTTPException(422, str(e).replace("ERROR: ", "")[:300])
    files = [f for f in glob(os.path.join(folder, "*")) if not f.endswith((".part", ".ytdl"))]
    if not files:
        raise HTTPException(500, "Download produced no file (maybe over size limit)")
    return max(files, key=os.path.getsize)


def mux(video: str, audio: str, out: str) -> bool:
    r = subprocess.run(
        ["ffmpeg", "-y", "-i", video, "-i", audio, "-map", "0:v:0", "-map", "1:a:0",
         "-c:v", "copy", "-c:a", "aac", "-shortest", "-movflags", "+faststart", out],
        capture_output=True, timeout=300,
    )
    return r.returncode == 0 and os.path.exists(out)


@app.get("/api/download", dependencies=[Depends(guard)])
def download(url: str, quality: str = Query("hd", pattern="^(hd|sd|audio)$")):
    """quality = hd | sd | audio. Video is always merged (video+audio) into one MP4."""
    if not _slots.acquire(timeout=30):
        raise HTTPException(503, "Server busy, try again shortly")
    tmp = tempfile.mkdtemp(prefix="dl_")
    try:
        opts = base_opts(
            max_filesize=MAX_FILESIZE_MB * 1024 * 1024,
            windowsfilenames=True,
        )
        audio_ok = True
        if quality == "audio":
            opts["format"] = "bestaudio/best"
            opts["postprocessors"] = [
                {"key": "FFmpegExtractAudio", "preferredcodec": "mp3", "preferredquality": "192"}
            ]
            media = "audio/mpeg"
            path = run_download(url, os.path.join(tmp, "a"), opts)
        else:
            h = HD_HEIGHT if quality == "hd" else SD_HEIGHT
            vdir = os.path.join(tmp, "v")
            # "res" = SHORT side of the video, so portrait reels (1080x1920) count as 1080p
            vopts = dict(opts, format="bv*+ba/b",
                         format_sort=[f"res:{h}", "vcodec:h264", "acodec:aac"],
                         merge_output_format="mp4")
            media = "video/mp4"
            path = run_download(url, vdir, vopts)

            if not has_audio(path):
                # Fallback 1: download the audio on its own and join it with ffmpeg
                try:
                    aopts = dict(opts, format="bestaudio/ba/b")
                    apath = run_download(url, os.path.join(tmp, "a"), aopts)
                    final = os.path.join(tmp, "final.mp4")
                    if has_audio(apath) and mux(path, apath, final):
                        path = final
                except HTTPException:
                    pass
            if not has_audio(path):
                # Fallback 2: any single file that already contains audio
                try:
                    sopts = dict(opts, format="b[acodec!=none][vcodec!=none]",
                                 merge_output_format="mp4")
                    spath = run_download(url, os.path.join(tmp, "s"), sopts)
                    if has_audio(spath):
                        path = spath
                except HTTPException:
                    pass
            audio_ok = has_audio(path)

        def cleanup():
            shutil.rmtree(tmp, ignore_errors=True)
            _slots.release()

        return FileResponse(
            path,
            media_type=media,
            filename=os.path.basename(path),
            headers={"X-Has-Audio": "true" if audio_ok else "false"},
            background=BackgroundTask(cleanup),
        )
    except Exception:
        shutil.rmtree(tmp, ignore_errors=True)
        _slots.release()
        raise
