import base64
import os
import re
import time
from urllib.parse import quote

import requests
import yt_dlp
from flask import Flask, Response, jsonify, redirect, request

app = Flask(__name__)

APP_VERSION = "process_false_v3_2026_06_18"

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
    # Importante:
    # Não forçamos "format" aqui.
    # Se colocar "22/18/..." direto no yt-dlp, ele pode abortar com:
    # "Requested format is not available"
    # antes do nosso fallback manual rodar.
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
                # Ajuda em alguns casos a expor formatos que o cliente padrão não mostra.
                "player_client": ["web", "mweb", "android"],
            }
        },
    }

    # Cookies opcionais.
    # Útil quando o YouTube exige: "Sign in to confirm you're not a bot".
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
    mime = (f.get("mime_type") or "").lower()
    fps = f.get("fps") or 0

    # Para PS3/Flash, o ideal é progressivo: vídeo+áudio no mesmo MP4.
    # DASH separado não serve para "fichier=".
    if acodec == "none" or vcodec == "none":
        return -1

    # Evita storyboard/imagem/etc.
    if ext in ("mhtml", "jpg", "png", "webp"):
        return -1

    s = 0

    # MP4 progressivo é rei.
    if ext == "mp4":
        s += 300
    if "mp4" in mime:
        s += 80
    if "avc1" in str(vcodec):
        s += 90
    if "mp4a" in str(acodec):
        s += 90

    # Evita HLS/DASH para PS3, mas deixa como último fallback para PC.
    if protocol in ("https", "http"):
        s += 80
    elif "m3u8" in protocol:
        s += 10 if mode == "pc" else -200
    elif "dash" in protocol:
        s -= 200

    # Formatos clássicos do YouTube.
    if format_id == "18":
        s += 500 if mode == "ps3" else 220
    elif format_id == "22":
        s += 420 if mode == "pc" else 180

    if height:
        if mode == "ps3":
            # PS3 Flash: 360p costuma ser mais seguro.
            if height <= 360:
                s += 180
            elif height <= 480:
                s += 100
            elif height <= 720:
                s += 40
            else:
                s -= 200
            s -= abs(height - 360) // 4
        else:
            # PC: 720p ok.
            if height <= 720:
                s += 160
            else:
                s -= 80
            s -= abs(height - 720) // 8

    # 60fps pode pesar no PS3.
    if mode == "ps3" and fps and fps > 30:
        s -= 80

    return s


def pick_best_progressive_format(info: dict, mode: str = "ps3"):
    formats = info.get("formats") or []
    scored = []

    for f in formats:
        s = format_score(f, mode)
        if s >= 0:
            scored.append((s, f))

    scored.sort(key=lambda x: x[0], reverse=True)

    if scored:
        return scored[0][1], scored

    # Fallback final para PC: qualquer URL tocável.
    if mode == "pc":
        any_url = [f for f in formats if f.get("url") and (f.get("vcodec") or "none") != "none"]
        if any_url:
            return any_url[-1], []

    return None, scored


def extract_mp4(video_id: str, mode: str = "ps3", force: bool = False) -> dict:
    if not valid_video_id(video_id):
        raise ValueError("ID inválido")

    mode = "pc" if mode == "pc" else "ps3"
    cache_key = f"{video_id}:{mode}:process_false_v3"

    if not force:
        cached = cache_get(cache_key)
        if cached:
            cached = dict(cached)
            cached["cache"] = True
            return cached

    watch_url = f"https://www.youtube.com/watch?v={video_id}"

    with yt_dlp.YoutubeDL(yt_dlp_opts(mode)) as ydl:
        info = ydl.extract_info(watch_url, download=False, process=False)

    selected, scored = pick_best_progressive_format(info, mode)

    if not selected:
        available = []
        for f in (info.get("formats") or [])[:80]:
            available.append({
                "format_id": f.get("format_id"),
                "ext": f.get("ext"),
                "height": f.get("height"),
                "vcodec": f.get("vcodec"),
                "acodec": f.get("acodec"),
                "protocol": f.get("protocol"),
            })

        raise RuntimeError(
            "Nenhum formato progressivo com vídeo+áudio foi encontrado. "
            "Abra /formats/%s?mode=%s para ver formatos disponíveis. "
            "Primeiros formatos: %s" % (video_id, mode, available[:12])
        )

    media_url = selected.get("url")
    if not media_url:
        raise RuntimeError("Formato escolhido não tem URL")

    result = {
        "id": video_id,
        "title": info.get("title") or "",
        "url": media_url,
        "format": selected.get("format_id") or selected.get("format") or "",
        "ext": selected.get("ext"),
        "height": selected.get("height"),
        "width": selected.get("width"),
        "vcodec": selected.get("vcodec"),
        "acodec": selected.get("acodec"),
        "protocol": selected.get("protocol"),
        "mode": mode,
        "duration": info.get("duration"),
        "thumbnail": info.get("thumbnail"),
        "cache": False,
        "created_at": int(time.time()),
        "available_progressive": [
            {
                "score": s,
                "format_id": f.get("format_id"),
                "ext": f.get("ext"),
                "height": f.get("height"),
                "vcodec": f.get("vcodec"),
                "acodec": f.get("acodec"),
                "protocol": f.get("protocol"),
            }
            for s, f in scored[:10]
        ],
    }

    cache_set(cache_key, result)
    return result


def list_formats_for_debug(video_id: str, mode: str = "ps3") -> dict:
    if not valid_video_id(video_id):
        raise ValueError("ID inválido")

    mode = "pc" if mode == "pc" else "ps3"
    watch_url = f"https://www.youtube.com/watch?v={video_id}"

    with yt_dlp.YoutubeDL(yt_dlp_opts(mode)) as ydl:
        info = ydl.extract_info(watch_url, download=False, process=False)

    rows = []
    for f in info.get("formats") or []:
        rows.append({
            "score": format_score(f, mode),
            "format_id": f.get("format_id"),
            "format_note": f.get("format_note"),
            "ext": f.get("ext"),
            "width": f.get("width"),
            "height": f.get("height"),
            "fps": f.get("fps"),
            "vcodec": f.get("vcodec"),
            "acodec": f.get("acodec"),
            "protocol": f.get("protocol"),
            "filesize": f.get("filesize") or f.get("filesize_approx"),
            "has_url": bool(f.get("url")),
        })

    rows.sort(key=lambda x: x["score"], reverse=True)

    return {
        "id": video_id,
        "title": info.get("title") or "",
        "mode": mode,
        "formats_count": len(rows),
        "best_candidates": rows[:40],
    }


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
  <h1>yt-dlp External Extractor</h1>\n  <p>Version: process_false_v3_2026_06_18</p>
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
    <p><code>/extract/7H6swK9OHC0?mode=ps3</code></p>\n    <p><code>/formats/7H6swK9OHC0?mode=ps3</code></p>\n    <p><code>/raw/7H6swK9OHC0?mode=ps3</code></p>\n    <p><code>/version</code></p>
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
        "version": APP_VERSION,
        "cache_items": len(CACHE),
        "has_cookies": has_cookies
    })


@app.get("/version")
def version():
    return jsonify({"ok": True, "version": APP_VERSION})


@app.get("/extract/<video_id>")
def extract_route(video_id):
    mode = request.args.get("mode", "ps3")
    force = request.args.get("force") == "1"

    try:
        return jsonify(extract_mp4(video_id, mode=mode, force=force))
    except Exception as e:
        return jsonify({"ok": False, "error": str(e), "id": video_id}), 500


@app.get("/formats/<video_id>")
def formats_route(video_id):
    mode = request.args.get("mode", "ps3")

    try:
        return jsonify(list_formats_for_debug(video_id, mode=mode))
    except Exception as e:
        return jsonify({"ok": False, "error": str(e), "id": video_id}), 500




@app.get("/raw/<video_id>")
def raw_route(video_id):
    mode = request.args.get("mode", "ps3")

    try:
        if not valid_video_id(video_id):
            raise ValueError("ID inválido")

        mode = "pc" if mode == "pc" else "ps3"
        watch_url = f"https://www.youtube.com/watch?v={video_id}"

        with yt_dlp.YoutubeDL(yt_dlp_opts(mode)) as ydl:
            info = ydl.extract_info(watch_url, download=False, process=False)

        formats = info.get("formats") or []
        return jsonify({
            "ok": True,
            "version": APP_VERSION,
            "id": video_id,
            "title": info.get("title") or "",
            "formats_count": len(formats),
            "top_keys": sorted(list(info.keys()))[:80],
            "first_formats": [
                {
                    "format_id": f.get("format_id"),
                    "ext": f.get("ext"),
                    "height": f.get("height"),
                    "vcodec": f.get("vcodec"),
                    "acodec": f.get("acodec"),
                    "protocol": f.get("protocol"),
                    "has_url": bool(f.get("url")),
                }
                for f in formats[:25]
            ]
        })
    except Exception as e:
        return jsonify({"ok": False, "version": APP_VERSION, "error": str(e), "id": video_id}), 500

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
