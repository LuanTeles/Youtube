// Cloudflare Worker snippet para chamar seu extrator externo.
// Troque EXTRACTOR_BASE pela URL do Render/Railway.
const EXTRACTOR_BASE = 'https://SEU-APP.up.railway.app';

async function getVideoUrlFromExternalExtractor(videoId, mode = 'ps3') {
  const res = await fetch(`${EXTRACTOR_BASE}/extract/${encodeURIComponent(videoId)}?mode=${encodeURIComponent(mode)}`, {
    headers: { 'Accept': 'application/json' }
  });

  if (!res.ok) {
    throw new Error('Extractor HTTP ' + res.status);
  }

  const data = await res.json();

  if (!data.url) {
    throw new Error(data.error || 'Extractor não retornou URL');
  }

  return data.url;
}

// Exemplo dentro do seu /watch:
const data = await fetch(`${EXTRACTOR_BASE}/extract/${videoId}?mode=ps3`).then(r => r.json());
if (!data.url) return new Response('Sem URL', { status: 500 });
return await proxyVideo(data.url, request);
