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


## Fix Railway mise Python attestations

Se o build falhar com:

`No GitHub artifact attestations found for python@3.11.9`

No Railway > Variables, adicione:

`MISE_PYTHON_GITHUB_ATTESTATIONS=false`

Este ZIP também inclui `mise.toml`:

```toml
[settings]
python.github_attestations = false
```

Depois faça Redeploy.


## YouTube pedindo "Sign in to confirm you're not a bot"

Se `/extract/ID` retornar erro pedindo cookies, use variável de ambiente no Railway.

### Variável recomendada

No Railway > Variables:

`YTDLP_COOKIES_B64=<seu cookies.txt em base64>`

O app vai escrever isso em `/tmp/youtube_cookies.txt` e passar para o yt-dlp.

### Como gerar base64 no Windows PowerShell

Com `cookies.txt` na pasta atual:

```powershell
[Convert]::ToBase64String([IO.File]::ReadAllBytes("cookies.txt")) | Set-Clipboard
```

Depois cole no Railway como valor de `YTDLP_COOKIES_B64`.

### Segurança

Cookies equivalem a sessão/login. Use conta secundária do YouTube/Google, não a principal.
Não poste cookies em chat, print, GitHub ou logs.


## Fix "Requested format is not available"

Esta versão não força mais `22/18/...` dentro do yt-dlp logo de cara.
Ela primeiro deixa o yt-dlp listar os formatos, depois o app escolhe manualmente:

- PS3: prioriza itag 18, MP4, avc1 + mp4a, progressivo.
- PC: prioriza itag 22/720p, mas cai para outros progressivos.

Novo endpoint de debug:

`/formats/7H6swK9OHC0?mode=ps3`

Use ele se `/extract` disser que nenhum progressivo apareceu.


## Versão process_false_v3

Se ainda aparecer `Requested format is not available`, essa versão usa:

```python
ydl.extract_info(url, download=False, process=False)
```

Isso evita o yt-dlp abortar na seleção automática de formato antes do app listar os formatos.

Teste depois do redeploy:

- `/version` deve retornar `process_false_v3_2026_06_18`
- `/raw/7H6swK9OHC0?mode=ps3`
- `/formats/7H6swK9OHC0?mode=ps3`
- `/extract/7H6swK9OHC0?mode=ps3&force=1`
