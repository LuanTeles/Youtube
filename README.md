# yt-dlp External Extractor para Railway/Render

Use apenas com vídeos seus, conteúdo livre, ou conteúdo que você tem direito de acessar/preservar.

## Endpoints

- `/extract/<video_id>?mode=ps3`
  Retorna JSON com URL MP4 progressiva.
- `/direct?v=<video_id>&mode=pc`
  Redireciona o PC para a URL real.
- `/proxy?v=<video_id>&mode=ps3`
  Faz proxy com Range para PS3/Flash.
- `/player?v=<video_id>`
  Abre player Flash usando `/proxy`.
- `/health`
  Teste do serviço.

## Modo

- `mode=ps3`: prioriza itag 18, MP4 360p progressivo.
- `mode=pc`: prioriza itag 22, MP4 720p progressivo.

## Deploy Railway

1. Crie um projeto no GitHub com estes arquivos.
2. Railway > New Project > Deploy from GitHub.
3. Start command:
   `gunicorn app:app --bind 0.0.0.0:$PORT --timeout 120`

## Deploy Render

1. New Web Service.
2. Runtime Python.
3. Build command:
   `pip install -r requirements.txt`
4. Start command:
   `gunicorn app:app --bind 0.0.0.0:$PORT --timeout 120`

## Observações

- Render Free pode dormir após inatividade; a primeira chamada pode demorar.
- Railway não tem plano grátis permanente; Hobby tem mínimo mensal.
- YouTube pode exigir PO Token/cookies em alguns casos. Este projeto não burla isso; só usa yt-dlp normalmente.
