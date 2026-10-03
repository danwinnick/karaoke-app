import { api, el, formatDate, handleAuthError, homeFor, toast } from './api.js';

const $ = (id) => document.getElementById(id);
const POLL_MS = 10000;
const SEARCH_POLL_MS = 400;
const SEARCH_TIMEOUT_MS = 20000;
const SOURCE_NAMES = { karafun: 'KaraFun', stingray: 'Stingray Karaoke', youtube: 'YouTube' };
const STATUS_TEXT = {
  queued: 'In line',
  playing: 'On stage now!',
  done: 'Sung',
  skipped: 'Skipped',
  cancelled: 'Removed',
};

let status = null;
let djs = [];
let here = null;
let selected = null;
let searchTimer;
let searchSeq = 0;
let activeSuggestion = -1;
let performancesCursor = null;

// ---- DJ picker -------------------------------------------------------------

function distanceMiles(a, b) {
  const rad = (d) => (d * Math.PI) / 180;
  const dLat = rad(b.lat - a.lat);
  const dLng = rad(b.lng - a.lng);
  const h = Math.sin(dLat / 2) ** 2 + Math.cos(rad(a.lat)) * Math.cos(rad(b.lat)) * Math.sin(dLng / 2) ** 2;
  return 3958.8 * 2 * Math.asin(Math.sqrt(h));
}

function renderDjOptions() {
  const list = djs.map((dj) => ({ ...dj, miles: here ? distanceMiles(here, dj) : null }));
  if (here) list.sort((a, b) => a.miles - b.miles);

  const select = $('dj-select');
  const current = status?.dj?.djId ?? '';
  select.replaceChildren(
    el('option', { value: '', disabled: true, selected: !current }, list.length ? 'Choose your DJ…' : 'No DJs yet'),
    ...list.map((dj) =>
      el(
        'option',
        { value: dj.djId, selected: dj.djId === current },
        dj.miles === null ? dj.name : `${dj.name} · ${dj.miles < 10 ? dj.miles.toFixed(1) : Math.round(dj.miles)} mi`,
      ),
    ),
  );
}

async function loadDjs() {
  ({ djs } = await api('/api/djs'));
  renderDjOptions();
  navigator.geolocation?.getCurrentPosition(
    (pos) => {
      here = { lat: pos.coords.latitude, lng: pos.coords.longitude };
      renderDjOptions();
    },
    () => {},
    { maximumAge: 600000, timeout: 8000 },
  );
}

$('dj-select').addEventListener('change', async (e) => {
  try {
    render(await api('/api/singer/dj', { method: 'POST', body: { djId: e.target.value } }));
    toast(`You're with ${status.dj.name} tonight`);
  } catch (err) {
    toast(err.message, { kind: 'error' });
  }
});

// ---- status ----------------------------------------------------------------

function render(next) {
  status = next;
  const { dj } = status;
  $('needs-dj').hidden = Boolean(dj);
  $('with-dj').hidden = !dj;
  $('dj-address').textContent = dj?.address ?? '';
  if ($('dj-select').value !== (dj?.djId ?? '')) $('dj-select').value = dj?.djId ?? '';
  if (!dj) return;

  $('position').textContent = status.position ?? '–';
  $('position-label').textContent =
    status.position === null
      ? status.nowPlaying?.isMe
        ? "You're on stage! 🎶"
        : 'Pick a song to get in line'
      : status.position === 1
        ? "You're up next!"
        : `in line of ${status.queueLength} for ${dj.name}`;
  $('now-playing').textContent =
    status.nowPlaying && !status.nowPlaying.isMe
      ? `Now singing: ${status.nowPlaying.singerName} — ${status.nowPlaying.title}`
      : '';

  const songs = status.songs.slice().reverse();
  $('my-songs').replaceChildren(
    ...(songs.length
      ? songs.map((song) =>
          el(
            'li',
            { class: `song song-${song.status}` },
            song.thumbnail ? el('img', { src: song.thumbnail, alt: '', loading: 'lazy' }) : el('div', { class: 'thumb-placeholder' }),
            el(
              'div',
              { class: 'song-body' },
              el('div', { class: 'song-title' }, song.title),
              el(
                'div',
                { class: 'song-meta' },
                song.status === 'queued' && song.position ? `#${song.position} in line` : STATUS_TEXT[song.status],
              ),
            ),
            song.status === 'queued'
              ? el('button', { class: 'icon-btn', 'aria-label': `Remove ${song.title}`, onclick: () => cancel(song) }, '✕')
              : null,
          ),
        )
      : [el('li', { class: 'empty' }, 'No songs yet tonight.')]),
  );

  const tipBtn = $('tip-btn');
  tipBtn.disabled = status.tipUsed || status.position === null;
  tipBtn.textContent = status.tipUsed ? '💲 Tipped tonight — thanks!' : '💲 Tip the DJ';
  $('tip-help').textContent = status.tipUsed
    ? 'One tip per night. Come back next time!'
    : status.position === null
      ? 'Get in line first, then tip once tonight to jump ahead.'
      : status.canBoost
        ? `One virtual tip per night. Jump from #${status.position} to #${status.tipPosition} and send ${dj.name} a 💲.`
        : `You're already in the top ${status.tipPosition}. A tip still sends ${dj.name} a 💲 (one per night).`;
}

async function refresh() {
  try {
    render(await api('/api/singer/status'));
  } catch (err) {
    if (!handleAuthError(err, 'singer')) console.error(err);
  }
}

async function cancel(song) {
  if (!confirm(`Remove "${song.title}" from the line?`)) return;
  try {
    render(await api('/api/singer/requests/cancel', { method: 'POST', body: { songId: song.songId } }));
  } catch (err) {
    toast(err.message, { kind: 'error' });
  }
}

$('tip-btn').addEventListener('click', async () => {
  const message = status.canBoost
    ? `Send ${status.dj.name} a virtual tip and move up to #${status.tipPosition}? You can only do this once tonight.`
    : `Send ${status.dj.name} a virtual tip? You can only do this once tonight.`;
  if (!confirm(message)) return;
  try {
    const result = await api('/api/singer/tip', { method: 'POST' });
    render(result);
    toast(result.boosted ? `💲 Tipped! You're now #${result.position}` : '💲 Tip sent!', { kind: 'success' });
  } catch (err) {
    toast(err.message, { kind: 'error' });
  }
});

// ---- song search -----------------------------------------------------------

function closeSuggestions() {
  $('suggestions').hidden = true;
  $('song-search').setAttribute('aria-expanded', 'false');
  activeSuggestion = -1;
}

function choose(result) {
  selected = result;
  closeSuggestions();
  $('song-search').value = result.title;
  $('song-name').value = result.title;
  // Only YouTube results have a picture.
  $('selected-thumb').hidden = !result.thumbnail;
  if (result.thumbnail) $('selected-thumb').src = result.thumbnail;
  $('selected').hidden = false;
}

function highlight(index) {
  const items = [...$('suggestions').children];
  if (!items.length) return;
  activeSuggestion = (index + items.length) % items.length;
  items.forEach((item, i) => item.setAttribute('aria-selected', String(i === activeSuggestion)));
  items[activeSuggestion].scrollIntoView({ block: 'nearest' });
}

// A note bouncing around inside a circle, shown while the lookup runs, over the name of the
// source being searched.
function searchingNote() {
  return el(
    'li',
    { class: 'suggestion-status searching', role: 'status', 'aria-label': 'Searching' },
    el(
      'span',
      { class: 'note-loader', 'aria-hidden': 'true' },
      el('span', { class: 'note-loader-x' }, el('span', { class: 'note-loader-note' }, '♪')),
    ),
    el('span', { class: 'searching-source' }),
  );
}

// A search is a file in S3 that the search state machine fills in as it looks on KaraFun, then
// Stingray, then YouTube. Re-reads it until its status changes from 'searching', and answers
// with the finished search, or null if the singer has typed something else since.
async function searchFinished(url, seq, onProgress) {
  const deadline = Date.now() + SEARCH_TIMEOUT_MS;
  while (seq === searchSeq) {
    const res = await fetch(url, { cache: 'no-store' });
    if (!res.ok) break;
    const found = await res.json();
    if (found.status !== 'searching') return found;
    onProgress(found);
    if (Date.now() > deadline) break;
    await new Promise((resolve) => setTimeout(resolve, SEARCH_POLL_MS));
  }
  if (seq !== searchSeq) return null;
  throw new Error('The search is taking too long. Try again.');
}

async function search(q) {
  const seq = ++searchSeq;
  const list = $('suggestions');
  const note = searchingNote();
  list.replaceChildren(note);
  list.hidden = false;
  try {
    const { url } = await api('/api/songs/search', { method: 'POST', body: { q } });
    const found = await searchFinished(url, seq, ({ source }) => {
      note.lastChild.textContent = SOURCE_NAMES[source] ? `Looking on ${SOURCE_NAMES[source]}…` : '';
    });
    if (!found || seq !== searchSeq) return;
    if (found.status === 'failed') throw new Error("We couldn't search for songs just now. Try again.");
    const { results } = found;
    list.replaceChildren(
      ...(results.length
        ? results.map((r) =>
            el(
              'li',
              { class: 'suggestion', role: 'option', 'aria-selected': 'false', onclick: () => choose(r) },
              r.thumbnail ? el('img', { src: r.thumbnail, alt: '', loading: 'lazy' }) : el('div', { class: 'thumb-placeholder' }, '🎤'),
              el('div', {}, el('div', { class: 'song-title' }, r.title), el('div', { class: 'song-meta' }, r.channel)),
            ),
          )
        : [el('li', { class: 'suggestion-status' }, 'No karaoke tracks found. Try different words.')]),
    );
    list._results = results;
    $('song-search').setAttribute('aria-expanded', 'true');
    // Auto-fill the song name with the best match while the singer keeps typing.
    if (results[0] && !selected) $('song-name').value = results[0].title;
  } catch (err) {
    if (seq === searchSeq) list.replaceChildren(el('li', { class: 'suggestion-status' }, err.message));
  }
}

$('song-search').addEventListener('input', (e) => {
  const q = e.target.value.trim();
  selected = null;
  $('selected').hidden = true;
  clearTimeout(searchTimer);
  if (q.length < 3) {
    closeSuggestions();
    return;
  }
  searchTimer = setTimeout(() => search(q), 450);
});

$('song-search').addEventListener('keydown', (e) => {
  const list = $('suggestions');
  if (list.hidden) return;
  if (e.key === 'ArrowDown') {
    e.preventDefault();
    highlight(activeSuggestion + 1);
  } else if (e.key === 'ArrowUp') {
    e.preventDefault();
    highlight(activeSuggestion - 1);
  } else if (e.key === 'Enter' && activeSuggestion >= 0) {
    e.preventDefault();
    choose(list._results[activeSuggestion]);
  } else if (e.key === 'Escape') {
    closeSuggestions();
  }
});

document.addEventListener('click', (e) => {
  if (!e.target.closest('.search')) closeSuggestions();
});

$('add-song').addEventListener('click', async () => {
  if (!selected) return;
  const button = $('add-song');
  button.disabled = true;
  try {
    const next = await api('/api/singer/requests', {
      method: 'POST',
      body: {
        source: selected.source,
        sourceId: selected.sourceId,
        videoId: selected.videoId,
        title: $('song-name').value.trim() || selected.title,
        thumbnail: selected.thumbnail,
      },
    });
    render(next);
    toast(`Added! You're #${next.position} in line`, { kind: 'success' });
    selected = null;
    $('selected').hidden = true;
    $('song-search').value = '';
  } catch (err) {
    toast(err.message, { kind: 'error' });
  } finally {
    button.disabled = false;
  }
});

// ---- performances -----------------------------------------------------------
// One per night: the set of songs picked on that date.

async function loadPerformances({ reset = false } = {}) {
  try {
    const cursor = reset ? null : performancesCursor;
    const page = await api(`/api/singer/performances${cursor ? `?cursor=${cursor}` : ''}`);
    performancesCursor = page.cursor;
    const container = $('performances');
    if (reset) container.replaceChildren();
    if (!page.performances.length && !container.children.length) {
      container.append(el('p', { class: 'card empty' }, 'No performances yet. Pick a song to start your first.'));
    }
    for (const performance of page.performances) {
      const count = performance.songs.length;
      container.append(
        el(
          'details',
          { class: 'card performance', open: !container.children.length },
          el(
            'summary',
            {},
            el(
              'span',
              { class: 'performance-head' },
              el('strong', {}, formatDate(performance.date)),
              el(
                'span',
                { class: 'muted small' },
                `${count} song${count === 1 ? '' : 's'} · ${performance.djName}${performance.tipped ? ' · 💲 tipped' : ''}`,
              ),
            ),
          ),
          el(
            'ul',
            { class: 'song-list' },
            performance.songs.map((song) =>
              el(
                'li',
                { class: `song song-${song.status}` },
                song.thumbnail ? el('img', { src: song.thumbnail, alt: '', loading: 'lazy' }) : el('div', { class: 'thumb-placeholder' }),
                el(
                  'div',
                  { class: 'song-body' },
                  el('div', { class: 'song-title' }, song.title),
                  el('div', { class: 'song-meta' }, STATUS_TEXT[song.status] ?? song.status),
                ),
              ),
            ),
          ),
        ),
      );
    }
    $('performances-more').hidden = !performancesCursor;
  } catch (err) {
    if (!handleAuthError(err, 'singer')) toast(err.message, { kind: 'error' });
  }
}

$('performances-more').addEventListener('click', () => loadPerformances());

document.querySelectorAll('.tab').forEach((tab) =>
  tab.addEventListener('click', () => {
    document.querySelectorAll('.tab').forEach((t) => t.classList.toggle('active', t === tab));
    $('tab-tonight').hidden = tab.dataset.tab !== 'tonight';
    $('tab-performances').hidden = tab.dataset.tab !== 'performances';
    // Reloaded on every visit so tonight's performance is current.
    if (tab.dataset.tab === 'performances') loadPerformances({ reset: true });
    window.scrollTo(0, 0);
  }),
);

// ---- boot ------------------------------------------------------------------

async function boot() {
  try {
    const me = await api('/api/me');
    if (me.role !== 'singer') {
      window.location.href = homeFor(me);
      return;
    }
    if (!me.singer) {
      window.location.href = '/singer-signup.html';
      return;
    }
    $('user-name').textContent = me.singer.name;
    await Promise.all([refresh(), loadDjs()]);
    renderDjOptions();
  } catch (err) {
    if (!handleAuthError(err, 'singer')) toast(err.message, { kind: 'error' });
  }
  setInterval(() => {
    if (document.visibilityState === 'visible') refresh();
  }, POLL_MS);
  document.addEventListener('visibilitychange', () => {
    if (document.visibilityState === 'visible') refresh();
  });
}

boot();
