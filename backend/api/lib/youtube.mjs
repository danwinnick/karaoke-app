const cache = new Map();
const CACHE_TTL_MS = 60 * 60 * 1000;

const ENTITIES = { amp: '&', lt: '<', gt: '>', quot: '"', apos: "'", '#39': "'", '#34': '"' };

export function decodeEntities(s) {
  return s.replace(/&(#?\w+);/g, (m, name) => {
    if (ENTITIES[name]) return ENTITIES[name];
    if (name.startsWith('#x')) return String.fromCodePoint(parseInt(name.slice(2), 16));
    if (name.startsWith('#')) return String.fromCodePoint(parseInt(name.slice(1), 10));
    return m;
  });
}

const NOISE = /\b(karaoke|instrumental|lyrics?|version|backing track|sing along|official|hd|hq|4k|no vocals?|with vocals?|key of \w+|in the style of)\b/i;

// "Adele - Someone Like You (Karaoke Version) | Sing King" -> "Adele - Someone Like You"
export function cleanTitle(raw) {
  let title = decodeEntities(raw).split(/\s[|•]\s/)[0];
  title = title.replace(/[([{【][^)\]}】]*[)\]}】]/g, (group) => (NOISE.test(group) ? '' : group));
  title = title.replace(/\b(karaoke version|karaoke|instrumental version|with lyrics)\b/gi, '');
  title = title.replace(/\s{2,}/g, ' ').replace(/[\s\-–—:]+$/, '').replace(/^[\s\-–—:]+/, '');
  return title.trim() || decodeEntities(raw);
}

export async function searchKaraoke(query, apiKey) {
  const key = query.toLowerCase();
  const hit = cache.get(key);
  if (hit && hit.expires > Date.now()) return hit.results;

  const params = new URLSearchParams({
    part: 'snippet',
    type: 'video',
    videoEmbeddable: 'true',
    maxResults: '8',
    q: `${query} karaoke`,
    key: apiKey,
  });
  const res = await fetch(`https://www.googleapis.com/youtube/v3/search?${params}`);
  if (!res.ok) throw new Error(`YouTube search failed: ${res.status} ${await res.text()}`);
  const data = await res.json();

  const results = (data.items ?? []).map((item) => ({
    videoId: item.id.videoId,
    title: cleanTitle(item.snippet.title),
    rawTitle: decodeEntities(item.snippet.title),
    channel: decodeEntities(item.snippet.channelTitle ?? ''),
    thumbnail: item.snippet.thumbnails?.medium?.url ?? item.snippet.thumbnails?.default?.url,
  }));

  if (cache.size > 500) cache.clear();
  cache.set(key, { results, expires: Date.now() + CACHE_TTL_MS });
  return results;
}
