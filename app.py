import base64
import html
import os
import re
import time
from urllib.parse import quote, urljoin

import requests
import yt_dlp
from flask import Flask, Response, jsonify, redirect, request

app = Flask(__name__)

APP_VERSION = "classic_auto_compat_v1_2026_06_18"

VIDEO_ID_RE = re.compile(r"^[A-Za-z0-9_-]{11}$")
CACHE = {}
CACHE_TTL_SECONDS = int(os.environ.get("CACHE_TTL_SECONDS", "900"))
REQUEST_TIMEOUT = int(os.environ.get("REQUEST_TIMEOUT", "30"))

USER_AGENT = os.environ.get(
    "USER_AGENT",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126 Safari/537.36"
)

DEFAULT_INSTANCES = [
    "https://inv.nadeko.net",
    "https://invidious.tiekoetter.com",
    "https://invidious.f5.si",
    "https://inv.thepixora.com",
    "https://yt.chocolatemoo53.com",
    "https://iv.datura.network",
    "https://iv.nboeck.de",
    "https://iv.melmac.space",
    "https://vid.puffyan.us",
]


def get_instances():
    raw = os.environ.get("INVIDIOUS_INSTANCES", "").strip()
    if not raw:
        return DEFAULT_INSTANCES

    items = []
    for part in raw.split(","):
        part = part.strip().rstrip("/")
        if part:
            items.append(part)
    return items or DEFAULT_INSTANCES


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

    with open(target, "w", encoding="utf-8") as f:
        f.write(content)

    os.chmod(target, 0o600)
    return target


def base_headers():
    return {
        "User-Agent": USER_AGENT,
        "Accept": "text/html,application/json,application/xhtml+xml,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9,pt-BR;q=0.8,pt;q=0.7",
        "Cache-Control": "no-cache",
        "Pragma": "no-cache",
    }


def safe_json_response(data, status=200):
    return jsonify(data), status


def score_media_url(url: str, mode: str) -> int:
    u = (url or "").lower()
    if not u:
        return -1

    s = 0

    if "itag=18" in u or "itag%3d18" in u:
        s += 500 if mode == "ps3" else 250
    if "itag=22" in u or "itag%3d22" in u:
        s += 420 if mode == "pc" else 150

    if "mime=video%2fmp4" in u or "mime=video/mp4" in u or "type=video/mp4" in u:
        s += 250
    if "/companion/latest_version" in u or "/latest_version" in u:
        s += 220
    if "/videoplayback" in u or "googlevideo.com/videoplayback" in u:
        s += 200
    if "ratebypass=yes" in u:
        s += 70
    if "check=" in u:
        s += 80

    if ".m3u8" in u or "manifest" in u or "dash" in u:
        s -= 300

    return s


def pick_best_stream(streams, mode):
    if not streams:
        return None

    def stream_score(f):
        itag = str(f.get("itag") or f.get("format_id") or "")
        url = f.get("url") or ""
        ext = (f.get("container") or f.get("ext") or "").lower()
        quality = (f.get("qualityLabel") or f.get("quality") or "").lower()
        mime = (f.get("type") or f.get("mimeType") or "").lower()

        s = score_media_url(url, mode)

        if itag == "18":
            s += 500 if mode == "ps3" else 250
        elif itag == "22":
            s += 420 if mode == "pc" else 160

        if "mp4" in ext or "mp4" in mime:
            s += 200
        if "360" in quality:
            s += 180 if mode == "ps3" else 60
        if "720" in quality:
            s += 180 if mode == "pc" else 50

        return s

    candidates = [f for f in streams if f.get("url")]
    if not candidates:
        return None

    candidates.sort(key=stream_score, reverse=True)
    return candidates[0]


def extract_from_invidious_api(video_id, mode, attempts):
    for base in get_instances():
        api_url = f"{base.rstrip('/')}/api/v1/videos/{video_id}"

        try:
            r = requests.get(api_url, headers=base_headers(), timeout=REQUEST_TIMEOUT)
            attempts.append({
                "method": "invidious_api",
                "url": api_url,
                "status": r.status_code,
                "type": r.headers.get("content-type", ""),
                "length": len(r.text or ""),
            })

            if r.status_code != 200:
                continue

            data = r.json()
            streams = data.get("formatStreams") or data.get("adaptiveFormats") or []
            best = pick_best_stream(streams, mode)

            if best and best.get("url"):
                return {
                    "id": video_id,
                    "title": data.get("title") or "",
                    "url": best["url"],
                    "format": str(best.get("itag") or ""),
                    "quality": best.get("qualityLabel") or best.get("quality") or "",
                    "source": "invidious_api",
                    "instance": base,
                    "mode": mode,
                }
        except Exception as e:
            attempts.append({
                "method": "invidious_api",
                "url": api_url,
                "error": str(e),
            })

    return None


def find_urls_in_html(page_html, base_url, mode):
    found = []

    # <source src="...">
    for m in re.finditer(r"<source\b[^>]*\bsrc=[\"']([^\"']+)[\"'][^>]*>", page_html, re.I):
        tag = m.group(0)
        src = html.unescape(m.group(1))
        full = urljoin(base_url, src)
        found.append({
            "url": full,
            "kind": "source",
            "score": score_media_url(full + " " + tag, mode)
        })

    # <meta property="og:video..." content="...">
    for m in re.finditer(r"<meta\b[^>]*(?:property|name)=[\"'](?:og:video(?::url|:secure_url)?|twitter:player)[\"'][^>]*\bcontent=[\"']([^\"']+)[\"'][^>]*>", page_html, re.I):
        src = html.unescape(m.group(1))
        full = urljoin(base_url, src)
        found.append({
            "url": full,
            "kind": "meta",
            "score": score_media_url(full, mode)
        })

    # Links diretos escapados no HTML.
    for m in re.finditer(r"""((?:https?:)?//[^"'<>\s]+/(?:companion/)?latest_version\?[^"'<>\s]+|(?:https?:)?//[^"'<>\s]+/videoplayback\?[^"'<>\s]+|/(?:companion/)?latest_version\?[^"'<>\s]+|/videoplayback\?[^"'<>\s]+)""", page_html, re.I):
        src = html.unescape(m.group(1)).replace("&amp;", "&")
        full = urljoin(base_url, src)
        found.append({
            "url": full,
            "kind": "raw",
            "score": score_media_url(full, mode)
        })

    # Remove duplicados preservando melhor score.
    by_url = {}
    for item in found:
        u = item["url"]
        if not u:
            continue
        if u not in by_url or item["score"] > by_url[u]["score"]:
            by_url[u] = item

    result = list(by_url.values())
    result.sort(key=lambda x: x["score"], reverse=True)
    return result


def extract_from_invidious_watch(video_id, mode, attempts):
    for base in get_instances():
        watch_url = f"{base.rstrip('/')}/watch?v={video_id}"

        try:
            r = requests.get(watch_url, headers=base_headers(), timeout=REQUEST_TIMEOUT)
            text = r.text or ""
            urls = find_urls_in_html(text, base, mode)

            attempts.append({
                "method": "invidious_watch",
                "url": watch_url,
                "status": r.status_code,
                "type": r.headers.get("content-type", ""),
                "length": len(text),
                "has_source": "<source" in text.lower(),
                "has_latest": "latest_version" in text,
                "has_check": "check=" in text,
                "candidates": len(urls),
                "top": urls[:3],
            })

            # Aceita HTML 200 e também alguns 500 que ainda carregam HTML com meta/source.
            if urls:
                best = urls[0]
                if best["score"] >= 0:
                    return {
                        "id": video_id,
                        "title": "",
                        "url": best["url"],
                        "format": "auto",
                        "quality": "auto",
                        "source": "invidious_watch_" + best["kind"],
                        "instance": base,
                        "mode": mode,
                    }
        except Exception as e:
            attempts.append({
                "method": "invidious_watch",
                "url": watch_url,
                "error": str(e),
            })

    return None


def yt_dlp_opts(mode: str = "ps3") -> dict:
    opts = {
        "quiet": True,
        "no_warnings": True,
        "noplaylist": True,
        "skip_download": True,
        "socket_timeout": REQUEST_TIMEOUT,
        "ignore_no_formats_error": True,
        "http_headers": {
            "User-Agent": USER_AGENT,
            "Accept-Language": "en-US,en;q=0.9",
        },
        "extractor_args": {
            "youtube": {
                "player_client": ["web", "mweb", "android"],
            }
        },
    }

    cookiefile = ensure_cookiefile_from_env()
    if cookiefile:
        opts["cookiefile"] = cookiefile

    return opts


def format_score(f: dict, mode: str = "ps3") -> int:
    url = f.get("url") or ""
    if not url:
        return -1

    ext = (f.get("ext") or "").lower()
    acodec = f.get("acodec") or "none"
    vcodec = f.get("vcodec") or "none"
    height = f.get("height") or 0
    format_id = str(f.get("format_id") or "")
    protocol = (f.get("protocol") or "").lower()

    if acodec == "none" or vcodec == "none":
        return -1
    if ext in ("mhtml", "jpg", "png", "webp"):
        return -1

    s = score_media_url(url, mode)

    if ext == "mp4":
        s += 300
    if "avc1" in str(vcodec):
        s += 90
    if "mp4a" in str(acodec):
        s += 90
    if protocol in ("https", "http"):
        s += 80
    if format_id == "18":
        s += 500 if mode == "ps3" else 220
    elif format_id == "22":
        s += 420 if mode == "pc" else 180

    if height:
        if mode == "ps3":
            if height <= 360:
                s += 180
            elif height <= 480:
                s += 100
            elif height <= 720:
                s += 40
            else:
                s -= 200
        else:
            if height <= 720:
                s += 160
            else:
                s -= 80

    return s


def extract_from_ytdlp(video_id, mode, attempts):
    watch_url = f"https://www.youtube.com/watch?v={video_id}"

    try:
        with yt_dlp.YoutubeDL(yt_dlp_opts(mode)) as ydl:
            info = ydl.extract_info(watch_url, download=False, process=False)

        formats = info.get("formats") or []
        candidates = []

        for f in formats:
            s = format_score(f, mode)
            if s >= 0:
                candidates.append((s, f))

        candidates.sort(key=lambda x: x[0], reverse=True)

        attempts.append({
            "method": "yt_dlp",
            "formats_count": len(formats),
            "usable_count": len(candidates),
            "first_formats": [
                {
                    "format_id": f.get("format_id"),
                    "ext": f.get("ext"),
                    "height": f.get("height"),
                    "vcodec": f.get("vcodec"),
                    "acodec": f.get("acodec"),
                    "protocol": f.get("protocol"),
                }
                for f in formats[:8]
            ],
        })

        if not candidates:
            return None

        best = candidates[0][1]

        return {
            "id": video_id,
            "title": info.get("title") or "",
            "url": best.get("url"),
            "format": best.get("format_id") or "",
            "quality": str(best.get("height") or ""),
            "source": "yt_dlp",
            "instance": "youtube",
            "mode": mode,
        }
    except Exception as e:
        attempts.append({
            "method": "yt_dlp",
            "error": str(e),
        })

    return None


def auto_extract(video_id: str, mode: str = "ps3", force: bool = False, include_attempts: bool = False) -> dict:
    if not valid_video_id(video_id):
        raise ValueError("ID inválido")

    mode = "pc" if mode == "pc" else "ps3"
    cache_key = f"auto:{video_id}:{mode}:classic_auto_compat_v1"

    if not force:
        cached = cache_get(cache_key)
        if cached:
            cached = dict(cached)
            cached["cache"] = True
            if not include_attempts:
                cached.pop("attempts", None)
            return cached

    attempts = []

    for extractor in (extract_from_invidious_api, extract_from_invidious_watch, extract_from_ytdlp):
        data = extractor(video_id, mode, attempts)
        if data and data.get("url"):
            data["cache"] = False
            data["created_at"] = int(time.time())
            data["attempts"] = attempts
            cache_set(cache_key, data)

            if not include_attempts:
                data = dict(data)
                data.pop("attempts", None)

            return data

    raise RuntimeError("Nenhum MP4 automático encontrado. Tentativas: " + str(attempts[-10:]))


@app.get("/")
def index():
    return Response(f"""<!doctype html>
<html>
<head>
  <meta charset="utf-8">
  <title>Classic Auto YouTube Extractor</title>
  <style>
    body{{background:#111124;color:#fff;font-family:Arial;padding:34px;text-align:center}}
    .box{{max-width:820px;margin:20px auto;background:rgba(255,255,255,.06);border:1px solid rgba(255,255,255,.12);border-radius:14px;padding:22px}}
    input,select{{padding:12px;border-radius:8px;border:1px solid #444;background:#000;color:#fff}}
    button,a{{display:inline-block;padding:12px 18px;border-radius:8px;background:#2c7dff;color:white;text-decoration:none;border:0;margin:8px;cursor:pointer}}
    .red{{background:#e32929}} .green{{background:#28a745}} .gray{{background:#555}}
    code{{background:#000;padding:3px 6px;border-radius:5px;word-break:break-all}}
  </style>
</head>
<body>
  <h1>Classic Auto YouTube Extractor</h1>
  <p>Version: {APP_VERSION}</p>

  <div class="box">
    <form action="/watch" method="get">
      <input name="v" value="7H6swK9OHC0" maxlength="11" placeholder="YouTube ID">
      <input type="hidden" name="direct" value="1">
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
    <p>Endpoints compatíveis com sua loja:</p>
    <p><code>/video/7H6swK9OHC0.mp4</code></p>
    <p><code>/watch?v=7H6swK9OHC0&direct=1</code></p>
    <p><code>/extract/7H6swK9OHC0?mode=ps3</code></p>
    <p><code>/debug?v=7H6swK9OHC0&mode=ps3</code></p>
    <p><code>/player?v=7H6swK9OHC0</code></p>
  </div>
</body>
</html>""", mimetype="text/html")


@app.get("/health")
def health():
    has_cookies = bool(
        os.environ.get("YTDLP_COOKIES_FILE") or
        os.environ.get("YTDLP_COOKIES_B64") or
        os.environ.get("YTDLP_COOKIES_RAW")
    )
    return jsonify({
        "ok": True,
        "service": "classic-auto-extractor",
        "version": APP_VERSION,
        "cache_items": len(CACHE),
        "has_cookies": has_cookies,
        "instances": get_instances(),
    })


@app.get("/version")
def version():
    return jsonify({"ok": True, "version": APP_VERSION})


@app.get("/extract/<video_id>")
def extract_route(video_id):
    mode = request.args.get("mode", "ps3")
    force = request.args.get("force") == "1"
    try:
        data = auto_extract(video_id, mode=mode, force=force, include_attempts=False)
        return jsonify(data)
    except Exception as e:
        return jsonify({"ok": False, "error": str(e), "id": video_id}), 500


@app.get("/debug")
def debug_route():
    video_id = request.args.get("v", "")
    mode = request.args.get("mode", "ps3")
    force = request.args.get("force") == "1"

    try:
        data = auto_extract(video_id, mode=mode, force=force, include_attempts=True)
        return jsonify({"ok": True, "result": data, "attempts": data.get("attempts", [])})
    except Exception as e:
        return jsonify({"ok": False, "version": APP_VERSION, "error": str(e), "id": video_id}), 500


@app.get("/watch")
def watch_route():
    video_id = request.args.get("v", "")
    mode = request.args.get("mode", "ps3")
    direct = request.args.get("direct") == "1"

    try:
        data = auto_extract(video_id, mode=mode, force=False, include_attempts=False)
        if direct:
            return redirect(data["url"], code=302)
        return proxy_url(data["url"])
    except Exception as e:
        return jsonify({"ok": False, "error": str(e), "id": video_id}), 500


@app.route("/video/<video_id>.mp4", methods=["GET", "HEAD"])
def classic_video_route(video_id):
    # ESTE é o endpoint que o seu classic já chama:
    # YOUTUBE_PS3_WORKER + '/video/' + videoId + '.mp4'
    try:
        data = auto_extract(video_id, mode="ps3", force=False, include_attempts=False)
        return proxy_url(data["url"])
    except Exception as e:
        return Response("Video extraction failed: " + str(e), status=500, mimetype="text/plain")


@app.route("/proxy", methods=["GET", "HEAD"])
def proxy_route():
    video_id = request.args.get("v", "")
    mode = request.args.get("mode", "ps3")

    try:
        data = auto_extract(video_id, mode=mode, force=False, include_attempts=False)
        return proxy_url(data["url"])
    except Exception as e:
        return jsonify({"ok": False, "error": str(e), "id": video_id}), 500


@app.get("/player")
def player_route():
    video_id = request.args.get("v", "")
    if not valid_video_id(video_id):
        return Response("ID inválido", status=400)

    video_url = f"/video/{quote(video_id)}.mp4"
    return Response(FLASH_PLAYER_HTML(video_url, video_id), mimetype="text/html")


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


def FLASH_PLAYER_HTML(video_url: str, video_id: str) -> str:
    return f"""<!DOCTYPE html>
<html>
<head>
  <meta charset="UTF-8">
  <title>PS3 Trailer</title>
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
        flashvars: "fichier={video_url}&auto_play=true&apercu=https://ps3-pro.github.io/Pages/resources/media/visualizer_preview.png"
      }};
      var attributes_936 = {{}};
      flashObject(
        "https://github.com/PS3-Pro/Pages/raw/main/resources/swf/video_player/video_player_27.swf",
        "player_936",
        "960",
        "540",
        "8",
        false,
        flashvars_936,
        params_936,
        attributes_936
      );
    </script>
  </div>
  <p style="font-size:12px;color:#888;word-break:break-all;">{video_url}</p>
</body>
</html>"""


if __name__ == "__main__":
    port = int(os.environ.get("PORT", "10000"))
    app.run(host="0.0.0.0", port=port)
