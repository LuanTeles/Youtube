import base64
import os
import re
import time
from urllib.parse import quote

import requests
import yt_dlp
from flask import Flask, Response, jsonify, redirect, request

app = Flask(__name__)

VIDEO_ID_RE = re.compile(r"^[A-Za-z0-9_-]{11}$")
CACHE = {}
CACHE_TTL_SECONDS = int(os.environ.get("CACHE_TTL_SECONDS", "900"))
REQUEST_TIMEOUT = int(os.environ.get("REQUEST_TIMEOUT", "30"))

USER_AGENT = os.environ.get(
    "USER_AGENT",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126 Safari/537.36"
)


def valid_video_id(video_id: str) -> bool:
    return bool(video_id and VIDEO_ID_RE.match(video_id))


def cache_get(key: str):
    item = CACHE.get(key)
    if not item:
        return None

    if time.time() - item["created_at"] > CACHE_TTL_SECONDS:
        CACHE.pop(key, None)
        return None

    return item["data"]


def cache_set(key: str, data: dict):
    CACHE[key] = {
        "created_at": time.time(),
        "data": data,
    }


def ensure_cookiefile_from_env():
    """
    Railway não é bom para subir arquivo cookies.txt manual.
    Então aceitamos:
    - YTDLP_COOKIES_FILE: caminho para arquivo já existente
    - YTDLP_COOKIES_B64: cookies.txt em base64
    - YTDLP_COOKIES_RAW: conteúdo bruto do cookies.txt
    """
    cookiefile = os.environ.get("YTDLP_COOKIES_FILE")
    if cookiefile and os.path.exists(cookiefile):
        return cookiefile

    b64 = os.environ.get("YTDLP_COOKIES_B64")
    raw = os.environ.get("YTDLP_COOKIES_RAW")

    if not b64 and not raw:
        return None

    target = "/tmp/youtube_cookies.txt"

    if b64:
        try:
            content = base64.b64decode(b64).decode("utf-8", errors="replace")
        except Exception as e:
            raise RuntimeError(f"YTDLP_COOKIES_B64 inválido: {e}")
    else:
        content = raw.replace("\\n", "\n")

    if "# Netscape HTTP Cookie File" not in content:
        # yt-dlp espera formato Netscape cookies.txt.
        # Não bloqueia, mas avisa pelo erro do yt-dlp se estiver errado.
        pass

    with open(target, "w", encoding="utf-8") as f:
        f.write(content)

    os.chmod(target, 0o600)
    return target


def yt_dlp_opts(mode: str = "ps3") -> dict:
    # PS3/Flash gosta de MP4 progressivo com vídeo+áudio juntos.
    # itag 18 = 360p MP4 H.264 + AAC, geralmente o mais compatível.
    # itag 22 = 720p MP4 H.264 + AAC, melhor para PC mas pode pesar no PS3.
    if mode == "pc":
        fmt = "22/18/best[ext=mp4][vcodec^=avc1][acodec^=mp4a][height<=720]/best[height<=720]/best"
    else:
        fmt = "18/22/best[ext=mp4][vcodec^=avc1][acodec^=mp4a][height<=720]/best[height<=720]/best"

    opts = {
        "format": fmt,
        "quiet": True,
        "no_warnings": True,
        "noplaylist": True,
        "skip_download": True,
        "socket_timeout": REQUEST_TIMEOUT,
        "http_headers": {
            "User-Agent": USER_AGENT,
            "Accept-Language": "en-US,en;q=0.9",
        },
    }

    # Cookies opcionais.
    # Útil quando o YouTube exige: "Sign in to confirm you're not a bot".
    cookiefile = ensure_cookiefile_from_env()
    if cookiefile:
        opts["cookiefile"] = cookiefile

    return opts


def extract_mp4(video_id: str, mode: str = "ps3", force: bool = False) -> dict:
    if not valid_video_id(video_id):
        raise ValueError("ID inválido")

    mode = "pc" if mode == "pc" else "ps3"
    cache_key = f"{video_id}:{mode}"

    if not force:
        cached = cache_get(cache_key)
        if cached:
            cached = dict(cached)
            cached["cache"] = True
            return cached

    watch_url = f"https://www.youtube.com/watch?v={video_id}"

    with yt_dlp.YoutubeDL(yt_dlp_opts(mode)) as ydl:
        info = ydl.extract_info(watch_url, download=False)

    # info["url"] normalmente vem quando o formato escolhido é único/progressivo.
    media_url = info.get("url")
    fmt = info.get("format_id") or info.get("format") or ""

    if not media_url:
        # Fallback defensivo: procura formato progressivo MP4 manualmente.
        formats = info.get("formats") or []

        def score(f):
            url = f.get("url") or ""
            ext = f.get("ext") or ""
            acodec = f.get("acodec") or "none"
            vcodec = f.get("vcodec") or "none"
            height = f.get("height") or 0
            format_id = str(f.get("format_id") or "")

            if not url:
                return -1
            if acodec == "none" or vcodec == "none":
                return -1

            s = 0
            if ext == "mp4":
                s += 100
            if "avc1" in vcodec:
                s += 50
            if "mp4a" in acodec:
                s += 50
            if format_id == "18":
                s += 200 if mode == "ps3" else 120
            if format_id == "22":
                s += 160 if mode == "pc" else 80
            if height and height <= 720:
                s += max(0, 80 - abs(height - (360 if mode == "ps3" else 720)) // 10)
            return s

        candidates = sorted(formats, key=score, reverse=True)
        if candidates and score(candidates[0]) >= 0:
            best = candidates[0]
            media_url = best.get("url")
            fmt = best.get("format_id") or best.get("format") or fmt

    if not media_url:
        raise RuntimeError("yt-dlp não retornou URL progressiva")

    result = {
        "id": video_id,
        "title": info.get("title") or "",
        "url": media_url,
        "format": fmt,
        "mode": mode,
        "duration": info.get("duration"),
        "thumbnail": info.get("thumbnail"),
        "cache": False,
        "created_at": int(time.time()),
    }

    cache_set(cache_key, result)
    return result


@app.get("/")
def index():
    return Response(
        f"""<!doctype html>
<html>
<head>
  <meta charset="utf-8">
  <title>yt-dlp External Extractor</title>
  <style>
    body{{background:#111124;color:#fff;font-family:Arial;padding:34px;text-align:center}}
    .box{{max-width:760px;margin:20px auto;background:rgba(255,255,255,.06);border:1px solid rgba(255,255,255,.12);border-radius:14px;padding:22px}}
    input,select{{padding:12px;border-radius:8px;border:1px solid #444;background:#000;color:#fff}}
    button,a{{display:inline-block;padding:12px 18px;border-radius:8px;background:#2c7dff;color:white;text-decoration:none;border:0;margin:8px;cursor:pointer}}
    .red{{background:#e32929}} .green{{background:#28a745}} .gray{{background:#555}}
    code{{background:#000;padding:3px 6px;border-radius:5px;word-break:break-all}}
  </style>
</head>
<body>
  <h1>yt-dlp External Extractor</h1>
  <div class="box">
    <form action="/direct" method="get">
      <input name="v" value="7H6swK9OHC0" maxlength="11" placeholder="YouTube ID">
      <select name="mode">
        <option value="ps3">PS3 360p/itag18</option>
        <option value="pc">PC 720p/itag22</option>
      </select>
      <button class="red" type="submit">Direto PC</button>
    </form>
    <form action="/player" method="get">
      <input name="v" value="7H6swK9OHC0" maxlength="11" placeholder="YouTube ID">
      <button class="green" type="submit">PS3 Flash</button>
    </form>
    <p><a class="gray" href="/health">Health</a></p>
  </div>
  <div class="box">
    <p>Endpoints:</p>
    <p><code>/extract/7H6swK9OHC0?mode=ps3</code></p>
    <p><code>/direct?v=7H6swK9OHC0&mode=pc</code></p>
    <p><code>/proxy?v=7H6swK9OHC0&mode=ps3</code></p>
    <p><code>/player?v=7H6swK9OHC0</code></p>
  </div>
</body>
</html>""",
        mimetype="text/html",
    )


@app.get("/health")
def health():
    has_cookies = bool(
        os.environ.get("YTDLP_COOKIES_FILE") or
        os.environ.get("YTDLP_COOKIES_B64") or
        os.environ.get("YTDLP_COOKIES_RAW")
    )

    return jsonify({
        "ok": True,
        "service": "yt-dlp-extractor",
        "cache_items": len(CACHE),
        "has_cookies": has_cookies
    })


@app.get("/extract/<video_id>")
def extract_route(video_id):
    mode = request.args.get("mode", "ps3")
    force = request.args.get("force") == "1"

    try:
        return jsonify(extract_mp4(video_id, mode=mode, force=force))
    except Exception as e:
        return jsonify({"ok": False, "error": str(e), "id": video_id}), 500


@app.get("/direct")
def direct_route():
    video_id = request.args.get("v", "")
    mode = request.args.get("mode", "ps3")

    try:
        data = extract_mp4(video_id, mode=mode)
        return redirect(data["url"], code=302)
    except Exception as e:
        return jsonify({"ok": False, "error": str(e), "id": video_id}), 500


@app.route("/proxy", methods=["GET", "HEAD"])
def proxy_route():
    video_id = request.args.get("v", "")
    mode = request.args.get("mode", "ps3")

    try:
        data = extract_mp4(video_id, mode=mode)
        return proxy_url(data["url"])
    except Exception as e:
        return jsonify({"ok": False, "error": str(e), "id": video_id}), 500


@app.get("/player")
def player_route():
    video_id = request.args.get("v", "")
    if not valid_video_id(video_id):
        return Response("ID inválido", status=400)

    proxy_url = f"/proxy?v={quote(video_id)}&mode=ps3"
    return Response(FLASH_PLAYER_HTML(proxy_url, video_id), mimetype="text/html")


def proxy_url(media_url: str):
    headers = {
        "User-Agent": request.headers.get("User-Agent") or USER_AGENT,
        "Accept": request.headers.get("Accept") or "*/*",
        "Accept-Language": request.headers.get("Accept-Language") or "en-US,en;q=0.9",
    }

    if request.headers.get("Range"):
        headers["Range"] = request.headers["Range"]

    upstream = requests.request(
        method="HEAD" if request.method == "HEAD" else "GET",
        url=media_url,
        headers=headers,
        stream=True,
        timeout=REQUEST_TIMEOUT,
        allow_redirects=True,
    )

    response_headers = {}
    for key in [
        "Content-Type",
        "Content-Length",
        "Content-Range",
        "Accept-Ranges",
        "Last-Modified",
        "ETag",
    ]:
        if key in upstream.headers:
            response_headers[key] = upstream.headers[key]

    response_headers["Access-Control-Allow-Origin"] = "*"
    response_headers["Access-Control-Allow-Headers"] = "Range"
    response_headers["Access-Control-Expose-Headers"] = "Content-Length, Content-Range, Accept-Ranges, Content-Type"
    response_headers["Accept-Ranges"] = "bytes"
    response_headers["Content-Disposition"] = 'inline; filename="video.mp4"'

    if request.method == "HEAD":
        upstream.close()
        return Response(status=upstream.status_code, headers=response_headers)

    def generate():
        try:
            for chunk in upstream.iter_content(chunk_size=1024 * 256):
                if chunk:
                    yield chunk
        finally:
            upstream.close()

    return Response(generate(), status=upstream.status_code, headers=response_headers)


def FLASH_PLAYER_HTML(proxy_url: str, video_id: str) -> str:
    return f"""<!DOCTYPE html>
<html>
<head>
  <meta charset="UTF-8">
  <title>PS3 yt-dlp Player</title>
</head>
<body style="background:#000;color:#fff;text-align:center;margin:0;padding-top:40px;font-family:Arial;">
  <script type="text/javascript" src="https://github.com/PS3-Pro/Pages/raw/main/resources/scripts/flash_objects.js"></script>
  <div id="player_936" align="center">
    <script type="text/javascript">
      var flashvars_936 = {{}};
      var params_936 = {{
        quality: "low",
        wmode: "transparent",
        bgcolor: "#000000",
        allowScriptAccess: "always",
        allowFullScreen: "true",
        flashvars: "fichier={proxy_url}&auto_play=true&apercu=https://ps3-pro.github.io/Pages/resources/media/visualizer_preview.png"
      }};
      var attributes_936 = {{}};
      flashObject(
        "https://github.com/PS3-Pro/Pages/raw/main/resources/swf/video_player/video_player_28.swf",
        "player_936",
        "1080",
        "660",
        "8",
        false,
        flashvars_936,
        params_936,
        attributes_936
      );
    </script>
  </div>
  <p style="font-size:12px;color:#888;word-break:break-all;">{proxy_url}</p>
</body>
</html>"""


if __name__ == "__main__":
    port = int(os.environ.get("PORT", "10000"))
    app.run(host="0.0.0.0", port=port)
