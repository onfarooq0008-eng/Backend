FROM python:3.12-slim

# ffmpeg is required to merge video+audio (Instagram etc.) and to make MP3
RUN apt-get update && apt-get install -y --no-install-recommends ffmpeg \
    && rm -rf /var/lib/apt/lists/* \
    && ffmpeg -version | head -n 1

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY main.py .
# optional: copies cookies.txt if you add it next to main.py
COPY cookies.tx[t] ./

ENV PORT=8000
EXPOSE 8000
CMD ["sh", "-c", "pip install -U -q 'yt-dlp[default]'; uvicorn main:app --host 0.0.0.0 --port ${PORT}"]
