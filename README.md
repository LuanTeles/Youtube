# Railway Classic Auto Compat

Esta versão foi feita para encaixar direto no código classic da loja.

O classic faz:
1. YouTube Data API busca o videoId pelo nome do jogo.
2. PS3 chama: YOUTUBE_PS3_WORKER + '/video/' + videoId + '.mp4'
3. Este Railway responde esse endpoint e tenta extrair MP4 automaticamente.

## Endpoints

- `/video/VIDEO_ID.mp4`
  Endpoint compatível com o classic antigo.

- `/watch?v=VIDEO_ID&direct=1`
  Redireciona PC para MP4 real.

- `/watch?v=VIDEO_ID`
  Proxy do vídeo.

- `/extract/VIDEO_ID?mode=ps3`
  JSON com URL extraída.

- `/debug?v=VIDEO_ID&mode=ps3&force=1`
  Mostra todas as tentativas.

- `/player?v=VIDEO_ID`
  Player Flash de teste.

## Ordem de extração automática

1. Invidious API `/api/v1/videos/ID`
2. Invidious watch HTML `/watch?v=ID`
3. yt-dlp com cookies, se configurado

## Variáveis Railway

Obrigatória se der erro do mise:

`MISE_PYTHON_GITHUB_ATTESTATIONS=false`

Opcional para yt-dlp:

`YTDLP_COOKIES_B64=<cookies.txt em base64>`

Opcional para trocar instâncias:

`INVIDIOUS_INSTANCES=https://inv.nadeko.net,https://invidious.f5.si`

## Patch no classic

Troque:

`var YOUTUBE_PS3_WORKER = "http://youtube.ps3-pro.workers.dev";`

por:

`var YOUTUBE_PS3_WORKER = "https://web-production-e2a34.up.railway.app";`

Depois o botão Trailer do classic deve continuar usando `/video/ID.mp4`.
