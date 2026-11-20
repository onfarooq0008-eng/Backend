# Media Downloader Backend (yt-dlp + FastAPI)

## Files
- `main.py` – the API
- `requirements.txt` – dependencies
- `Dockerfile` – includes ffmpeg (needed for merging video+audio and MP3)

## Deploy (no PC needed)
1. Create a GitHub repo and upload these 3 files to the root.
2. On a free host that supports Docker (Render, Koyeb, Hugging Face Spaces, etc.), create a new Web Service from that repo. It will detect the `Dockerfile`.
3. Set environment variables (all optional, no API key needed):
   - `RATE_LIMIT_PER_MIN` = 30
   - `MAX_PARALLEL_DOWNLOADS` = 2
   - `MAX_FILESIZE_MB` = 500
   - `HD_MAX_HEIGHT` = 1080, `SD_MAX_HEIGHT` = 480
   - `COOKIES_TXT` / `COOKIES_FILE` (see Cookies below)
4. Your API base URL will be something like `https://your-app.onrender.com`.

The container updates yt-dlp on every start, so just restart the service when a site stops working.

## Endpoints
No API key needed.

| Method | Path | Purpose |
|---|---|---|
| GET | `/health` | Status, yt-dlp version, ffmpeg and cookies check |
| GET/POST | `/api/info` | Title, thumbnail, duration + the 3 options (HD, SD, Audio) |
| GET | `/api/download?url=...&quality=hd` | Downloads, merges and streams the file |

`quality` values:
- `hd` - best up to 1080p, MP4 with audio merged in
- `sd` - up to 480p, MP4 with audio merged in
- `audio` - MP3

Video and audio streams (Instagram, YouTube, etc.) are merged by ffmpeg on the server, so the file you receive always has sound.

### Examples
```
curl "https://HOST/api/info?url=https://www.instagram.com/reel/XXXX/"
curl -o video.mp4 "https://HOST/api/download?url=URL&quality=hd"
curl -o audio.mp3 "https://HOST/api/download?url=URL&quality=audio"
```

### Cookies (Instagram and login-gated sites)
Use ONE of these:
1. Put a `cookies.txt` (Netscape format) next to `main.py` in the repo (the Dockerfile copies it automatically).
2. Set env var `COOKIES_TXT` to the full contents of the cookies file.
3. Set env var `COOKIES_FILE` to a path on the server.

Check `/health`: `"cookies_loaded": true` means it was found.

### Android (Kotlin / OkHttp)
```kotlin
val req = Request.Builder()
    .url("$BASE/api/download?url=${URLEncoder.encode(link, "UTF-8")}&quality=hd")
    .build()
// stream response.body to a file / MediaStore
```

## Notes
- Free hosts sleep when idle; the first request can take ~30-60 s. Use a long read timeout in the app (e.g. 120 s).
- Free tiers have limited bandwidth and disk, so keep `MAX_PARALLEL_DOWNLOADS` low.
- Some sites (YouTube especially) block datacenter IPs; if that happens you may need `cookies.txt`.
- Only download content you have the right to download.
