import { api, el, formatDate, handleAuthError, homeFor, toast } from './api.js';

const $ = (id) => document.getElementById(id);
const POLL_MS = 4000;
const OVERLAY_COUNT = 5;
// Songs found on these have no video: the DJ plays them in that service's own player.
const SOURCE_NAMES = { karafun: 'KaraFun', stingray: 'Stingray Karaoke' };

let state = null;
let player = null;
let playerReady = null;
let loadedVideoId = null;
let seenTips = null;
let busy = false;

// ---- rendering -------------------------------------------------------------

function tipBadge(entry) {
  return entry.tipped ? el('span', { class: 'tip-badge', title: 'Tipped tonight' }, '💲') : null;
}

function sourceBadge(entry) {
  return SOURCE_NAMES[entry.source] ? el('span', { class: 'source-badge' }, SOURCE_NAMES[entry.source]) : null;
}

function render(next) {
  state = next;
  $('dj-name').textContent = state.dj.nickname ?? state.dj.name;
  $('night').textContent = formatDate(state.date);
  $('queue-count').textContent = `(${state.queue.length})`;

  const now = state.nowPlaying;
  $('now').replaceChildren(
    now
      ? el(
          'div',
          { class: 'now-inner' },
          now.thumbnail ? el('img', { src: now.thumbnail, alt: '' }) : null,
          el(
            'div',
            {},
            el('div', { class: 'now-singer' }, now.singerName, ' ', tipBadge(now)),
            el('div', { class: 'now-title' }, now.title, ' ', sourceBadge(now)),
          ),
        )
      : el('p', { class: 'muted' }, state.queue.length ? 'Press Next singer to start.' : 'Nobody in line yet.'),
  );
  $('open-player').disabled = !now;
  $('next-btn').textContent = now ? '⏭ Next singer' : '▶ Start first singer';
  $('next-btn').disabled = !now && !state.queue.length;

  $('queue').replaceChildren(
    ...(state.queue.length
      ? state.queue.map((entry) =>
          el(
            'li',
            { class: `queue-row${entry.tipped ? ' tipped' : ''}` },
            el('span', { class: 'queue-pos' }, entry.position),
            entry.thumbnail ? el('img', { src: entry.thumbnail, alt: '', loading: 'lazy' }) : el('div', { class: 'thumb-placeholder' }),
            el(
              'div',
              { class: 'queue-body' },
              el('div', { class: 'queue-singer' }, entry.singerName, ' ', tipBadge(entry)),
              el('div', { class: 'queue-song' }, entry.title, ' ', sourceBadge(entry)),
            ),
            el(
              'div',
              { class: 'queue-actions' },
              el('button', { class: 'btn btn-small btn-primary', onclick: () => setStatus(entry, 'playing', true) }, '▶ Play'),
              el('button', { class: 'btn btn-small btn-ghost', onclick: () => setStatus(entry, 'skipped') }, 'Skip'),
            ),
          ),
        )
      : [el('li', { class: 'empty' }, 'The line is empty.')]),
  );

  $('tips').replaceChildren(
    ...(state.tips.length
      ? state.tips
          .slice()
          .reverse()
          .map((t) =>
            el('li', {}, '💲 ', el('strong', {}, t.singerName), ' ', el('span', { class: 'muted small' }, new Date(t.at).toLocaleTimeString([], { hour: 'numeric', minute: '2-digit' }))),
          )
      : [el('li', { class: 'muted' }, 'No tips yet.')]),
  );
  $('tip-count').hidden = !state.tips.length;
  $('tip-count').textContent = `💲 × ${state.tips.length}`;

  announceNewTips();
  renderOverlay();
}

function renderOverlay() {
  const now = state.nowPlaying;
  $('overlay-now').replaceChildren(
    now ? el('span', {}, '🎤 ', el('strong', {}, now.singerName), ` — ${now.title}`) : '',
  );
  $('overlay-next').replaceChildren(
    ...state.queue.slice(0, OVERLAY_COUNT).map((entry) =>
      el(
        'li',
        {},
        el('span', { class: 'overlay-singer' }, entry.singerName, entry.tipped ? ' 💲' : ''),
        el('span', { class: 'overlay-song' }, entry.title),
      ),
    ),
  );
  if (!$('player-modal').hidden) {
    if (now) showTrack(now);
    else closePlayer();
  }
}

// Shows a money sign for every tip the DJ hasn't seen yet (persisted per night).
function announceNewTips() {
  const key = `karaoke-seen-tips-${state.date}`;
  if (!seenTips) seenTips = new Set(JSON.parse(localStorage.getItem(key) ?? '[]'));
  const fresh = state.tips.filter((t) => !seenTips.has(`${t.singerId}:${t.at}`));
  for (const t of fresh) {
    seenTips.add(`${t.singerId}:${t.at}`);
    showMoney(t.singerName);
  }
  if (fresh.length) localStorage.setItem(key, JSON.stringify([...seenTips]));
}

function showMoney(singerName) {
  const target = $('player-modal').hidden ? document.body : $('overlay-tips');
  const node = el('div', { class: 'money-burst' }, el('span', { class: 'money-sign' }, '💲'), el('span', {}, `${singerName} tipped!`));
  target.append(node);
  setTimeout(() => node.remove(), 6000);
}

// ---- actions ---------------------------------------------------------------

async function withBusy(fn) {
  if (busy) return;
  busy = true;
  document.querySelectorAll('button').forEach((b) => b.classList.add('is-busy'));
  try {
    render(await fn());
  } catch (err) {
    if (!handleAuthError(err, 'dj')) toast(err.message, { kind: 'error' });
  } finally {
    busy = false;
    document.querySelectorAll('button').forEach((b) => b.classList.remove('is-busy'));
  }
}

function setStatus(entry, status, open = false) {
  return withBusy(async () => {
    const next = await api('/api/dj/status', {
      method: 'POST',
      body: { singerId: entry.singerId, songId: entry.songId, status },
    });
    if (open && next.nowPlaying) openPlayer(next.nowPlaying);
    return next;
  });
}

function nextSinger() {
  return withBusy(async () => {
    const next = await api('/api/dj/next', { method: 'POST' });
    if (next.nowPlaying) openPlayer(next.nowPlaying);
    else closePlayer();
    return next;
  });
}

async function refresh() {
  if (busy) return;
  try {
    render(await api('/api/dj/queue'));
  } catch (err) {
    if (!handleAuthError(err, 'dj')) console.error(err);
  }
}

// ---- player ----------------------------------------------------------------

function loadYouTubeApi() {
  playerReady ??= new Promise((resolve) => {
    window.onYouTubeIframeAPIReady = () => {
      player = new YT.Player('player', {
        width: '100%',
        height: '100%',
        playerVars: { autoplay: 1, rel: 0, fs: 0, modestbranding: 1, playsinline: 1, origin: location.origin },
        events: {
          onReady: () => resolve(player),
          onStateChange: (e) => {
            if (e.data === YT.PlayerState.ENDED) toast('Song finished — press Next singer when ready');
          },
        },
      });
    };
    const script = document.createElement('script');
    script.src = 'https://www.youtube.com/iframe_api';
    document.head.append(script);
  });
  return playerReady;
}

async function playVideo(videoId) {
  loadedVideoId = videoId;
  const p = await loadYouTubeApi();
  if (loadedVideoId === videoId) p.loadVideoById(videoId);
}

// Plays a YouTube track, or covers the video with where to play a KaraFun or Stingray one.
function showTrack(now) {
  const external = $('player-external');
  external.hidden = Boolean(now.videoId);
  if (now.videoId) {
    if (now.videoId !== loadedVideoId) playVideo(now.videoId);
    return;
  }
  if (loadedVideoId) player?.stopVideo?.();
  loadedVideoId = null;
  external.replaceChildren(
    el('div', { class: 'player-external-source' }, `Play on ${SOURCE_NAMES[now.source] ?? 'your karaoke player'}`),
    el('div', { class: 'player-external-title' }, now.title),
  );
}

function openPlayer(now) {
  $('player-modal').hidden = false;
  document.body.classList.add('modal-open');
  showTrack(now);
}

function closePlayer() {
  if ($('player-modal').hidden) return;
  $('player-modal').hidden = true;
  document.body.classList.remove('modal-open');
  player?.pauseVideo?.();
  if (document.fullscreenElement) document.exitFullscreen();
}

$('next-btn').addEventListener('click', nextSinger);
$('player-next').addEventListener('click', nextSinger);
$('open-player').addEventListener('click', () => state.nowPlaying && openPlayer(state.nowPlaying));
$('player-close').addEventListener('click', closePlayer);
// Fullscreen the whole modal (not the iframe) so the up-next overlay stays visible.
$('player-fullscreen').addEventListener('click', () => {
  const frame = document.querySelector('.player-frame');
  if (document.fullscreenElement) document.exitFullscreen();
  else frame.requestFullscreen?.() ?? frame.webkitRequestFullscreen?.();
});
document.addEventListener('keydown', (e) => {
  if (e.key === 'Escape' && !document.fullscreenElement) closePlayer();
});

// ---- boot ------------------------------------------------------------------

async function boot() {
  try {
    const me = await api('/api/me');
    if (me.role !== 'dj') {
      window.location.href = homeFor(me);
      return;
    }
    if (!me.dj) {
      window.location.href = '/dj-signup.html';
      return;
    }
    const first = await api('/api/dj/queue');
    // Don't replay tips from earlier in the night after a page reload.
    const key = `karaoke-seen-tips-${first.date}`;
    if (localStorage.getItem(key) === null) {
      localStorage.setItem(key, JSON.stringify(first.tips.map((t) => `${t.singerId}:${t.at}`)));
    }
    render(first);
  } catch (err) {
    if (!handleAuthError(err, 'dj')) toast(err.message, { kind: 'error' });
  }
  setInterval(refresh, POLL_MS);
}

boot();
