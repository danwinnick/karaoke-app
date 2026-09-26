import crypto from 'node:crypto';
import * as db from './lib/db.mjs';
import {
  HttpError,
  clearCookie,
  json,
  parseBody,
  parseCookies,
  redirect,
  requireString,
  setCookie,
} from './lib/http.mjs';
import { nightDate } from './lib/night.mjs';
import { TIP_POSITION, boostOrder, buildQueue, singerPosition, tipsForDj } from './lib/queue.mjs';
import { signToken, verifyToken } from './lib/session.mjs';
import { authorizeUrl, decodeJwt, ensureMembership, exchangeCode, getUser, pkcePair } from './lib/workos.mjs';
import { searchKaraoke } from './lib/youtube.mjs';

const SECRET = process.env.SESSION_SECRET;
const REDIRECT_URI = `${process.env.PUBLIC_URL}/auth/callback`;
const SESSION_COOKIE = 'karaoke_session';
const OAUTH_COOKIE = 'karaoke_oauth';
const SESSION_TTL = 7 * 24 * 3600;
const OAUTH_TTL = 600;
const MAX_QUEUED_PER_SINGER = 3;

const tonight = () => nightDate(new Date(), process.env.NIGHT_TIMEZONE);

// ---- auth helpers ----------------------------------------------------------

function getSession(event) {
  return verifyToken(parseCookies(event)[SESSION_COOKIE], SECRET);
}

function requireRole(event, role) {
  const session = getSession(event);
  if (!session) throw new HttpError(401, 'Please log in');
  if (role && session.role !== role) throw new HttpError(403, `Log in as a ${role} to do that`);
  return session;
}

async function requireDj(event) {
  const session = requireRole(event, 'dj');
  const dj = await db.getDj(session.sub);
  if (!dj) throw new HttpError(403, 'Finish DJ signup first');
  return { session, dj };
}

// ---- /auth -----------------------------------------------------------------

async function login(event) {
  const q = event.queryStringParameters ?? {};
  const role = q.role === 'dj' ? 'dj' : 'singer';
  const { verifier, challenge } = pkcePair();
  const state = crypto.randomBytes(16).toString('base64url');
  const oauthCookie = signToken({ state, verifier, role }, SECRET, OAUTH_TTL);
  const url = await authorizeUrl({ state, challenge, redirectUri: REDIRECT_URI, signup: q.signup === '1' });
  return redirect(url, [setCookie(OAUTH_COOKIE, oauthCookie, OAUTH_TTL)]);
}

async function callback(event) {
  const q = event.queryStringParameters ?? {};
  if (q.error) return redirect(`/?error=${encodeURIComponent(q.error_description ?? q.error)}`);

  const oauth = verifyToken(parseCookies(event)[OAUTH_COOKIE], SECRET);
  if (!oauth || !q.code || oauth.state !== q.state) {
    return redirect(`/?error=${encodeURIComponent('Your login expired, please try again')}`);
  }

  const tokens = await exchangeCode({ code: q.code, verifier: oauth.verifier, redirectUri: REDIRECT_URI });
  const claims = decodeJwt(tokens.id_token ?? tokens.access_token);
  const userId = claims.sub;

  let user = null;
  try {
    user = await getUser(userId);
  } catch (err) {
    console.warn('Could not load WorkOS user', err.message);
  }
  try {
    await ensureMembership(userId);
  } catch (err) {
    console.warn('Could not add organization membership', err.message);
  }

  const email = user?.email ?? claims.email ?? '';
  const name =
    [user?.first_name, user?.last_name].filter(Boolean).join(' ') || claims.name || email.split('@')[0] || 'Singer';

  let destination;
  if (oauth.role === 'dj') {
    destination = (await db.getDj(userId)) ? '/dj.html' : '/dj-signup.html';
  } else {
    await db.upsertSinger({ singerId: userId, name, email });
    destination = '/singer.html';
  }

  const session = signToken({ sub: userId, email, name, role: oauth.role }, SECRET, SESSION_TTL);
  return redirect(destination, [setCookie(SESSION_COOKIE, session, SESSION_TTL), clearCookie(OAUTH_COOKIE)]);
}

async function logout() {
  return redirect('/', [clearCookie(SESSION_COOKIE)]);
}

// ---- shared ----------------------------------------------------------------

async function config() {
  return json(200, { googleMapsApiKey: process.env.GOOGLE_MAPS_API_KEY });
}

async function me(event) {
  const session = requireRole(event);
  const dj = session.role === 'dj' ? await db.getDj(session.sub) : null;
  return json(200, {
    user: { id: session.sub, name: session.name, email: session.email },
    role: session.role,
    dj,
  });
}

async function listDjs(event) {
  requireRole(event);
  const djs = await db.listDjs();
  djs.sort((a, b) => a.name.localeCompare(b.name));
  return json(200, { djs });
}

// ---- DJ --------------------------------------------------------------------

async function djProfile(event) {
  const session = requireRole(event, 'dj');
  const body = parseBody(event);
  const lat = Number(body.lat);
  const lng = Number(body.lng);
  if (!Number.isFinite(lat) || !Number.isFinite(lng)) {
    throw new HttpError(400, 'Pick your address from the suggestions');
  }
  const email = requireString(body.email, 'Email', 254);
  if (!/^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(email)) throw new HttpError(400, 'Enter a valid email');

  const dj = await db.putDj({
    djId: session.sub,
    name: requireString(body.name, 'Name', 100),
    email,
    address: requireString(body.address, 'Address', 300),
    placeId: typeof body.placeId === 'string' ? body.placeId.slice(0, 300) : undefined,
    lat,
    lng,
  });
  return json(200, { dj });
}

async function djState(djId) {
  const date = tonight();
  const items = await db.queryDjNight(djId, date);
  const { nowPlaying, queue } = buildQueue(items, djId);
  return { date, nowPlaying, queue, tips: tipsForDj(items, djId) };
}

async function setSongStatus(entry, date, status) {
  const now = new Date().toISOString();
  const set = { status };
  if (status === 'playing') set.startedAt = now;
  if (status === 'done' || status === 'skipped') set.finishedAt = now;
  await db.updateSong({ singerId: entry.singerId, date, index: entry.index, songId: entry.songId, set });
}

async function djQueue(event) {
  const { dj } = await requireDj(event);
  return json(200, { dj, ...(await djState(dj.djId)) });
}

async function djNext(event) {
  const { dj } = await requireDj(event);
  const state = await djState(dj.djId);
  if (state.nowPlaying) await setSongStatus(state.nowPlaying, state.date, 'done');
  if (state.queue[0]) await setSongStatus(state.queue[0], state.date, 'playing');
  return json(200, { dj, ...(await djState(dj.djId)) });
}

async function djSongStatus(event) {
  const { dj } = await requireDj(event);
  const body = parseBody(event);
  const status = body.status;
  if (!['playing', 'done', 'skipped'].includes(status)) throw new HttpError(400, 'Invalid status');

  const state = await djState(dj.djId);
  const all = [...state.queue, ...(state.nowPlaying ? [state.nowPlaying] : [])];
  const entry = all.find((e) => e.songId === body.songId && e.singerId === body.singerId);
  if (!entry) throw new HttpError(404, 'That song is no longer in your queue');

  if (status === 'playing' && state.nowPlaying && state.nowPlaying.songId !== entry.songId) {
    await setSongStatus(state.nowPlaying, state.date, 'done');
  }
  await setSongStatus(entry, state.date, status);
  return json(200, { dj, ...(await djState(dj.djId)) });
}

// ---- Singer ----------------------------------------------------------------

async function singerStatus(singerId) {
  const date = tonight();
  const singer = await db.getSinger(singerId);
  const dj = singer?.currentDjId ? await db.getDj(singer.currentDjId) : null;
  const mine = await db.getNight(singerId, date);

  let queue = [];
  let nowPlaying = null;
  if (dj) ({ queue, nowPlaying } = buildQueue(await db.queryDjNight(dj.djId, date), dj.djId));

  const positions = new Map(queue.map((e) => [e.songId, e.position]));
  const position = singerPosition(queue, singerId);
  return {
    date,
    dj: dj && { djId: dj.djId, name: dj.name, address: dj.address },
    position,
    queueLength: queue.length,
    nowPlaying: nowPlaying && {
      singerName: nowPlaying.singerName,
      title: nowPlaying.title,
      isMe: nowPlaying.singerId === singerId,
    },
    songs: (mine?.songs ?? []).map((song) => ({
      songId: song.songId,
      title: song.title,
      videoId: song.videoId,
      thumbnail: song.thumbnail,
      status: song.status,
      requestedAt: song.requestedAt,
      position: positions.get(song.songId) ?? null,
      withCurrentDj: song.djId === dj?.djId,
    })),
    tipUsed: Boolean(mine?.tip),
    tipPosition: TIP_POSITION,
    canBoost: position !== null && position > TIP_POSITION,
  };
}

async function getStatus(event) {
  const session = requireRole(event, 'singer');
  return json(200, await singerStatus(session.sub));
}

async function chooseDj(event) {
  const session = requireRole(event, 'singer');
  const djId = requireString(parseBody(event).djId, 'DJ', 200);
  if (!(await db.getDj(djId))) throw new HttpError(404, 'DJ not found');

  await db.setSingerDj(session.sub, djId);
  const mine = await db.getNight(session.sub, tonight());
  if (mine && mine.djId !== djId) await db.moveNightToDj(mine, djId);
  return json(200, await singerStatus(session.sub));
}

async function youtubeSearch(event) {
  requireRole(event);
  const q = (event.queryStringParameters?.q ?? '').trim();
  if (q.length < 2 || q.length > 100) throw new HttpError(400, 'Search must be 2-100 characters');
  return json(200, { results: await searchKaraoke(q, process.env.YOUTUBE_API_KEY) });
}

async function requestSong(event) {
  const session = requireRole(event, 'singer');
  const body = parseBody(event);
  const videoId = requireString(body.videoId, 'Video', 20);
  if (!/^[\w-]{11}$/.test(videoId)) throw new HttpError(400, 'Invalid video');
  const title = requireString(body.title, 'Song name', 200);
  const thumbnail =
    typeof body.thumbnail === 'string' && /^https:\/\/i\d?\.ytimg\.com\//.test(body.thumbnail)
      ? body.thumbnail
      : undefined;

  const singer = await db.getSinger(session.sub);
  const djId = singer?.currentDjId;
  if (!djId) throw new HttpError(400, 'Pick a DJ first');

  const date = tonight();
  const mine = await db.getNight(session.sub, date);
  if (mine && mine.djId !== djId) await db.moveNightToDj(mine, djId);

  const queued = (mine?.songs ?? []).filter((s) => s.status === 'queued').length;
  if (queued >= MAX_QUEUED_PER_SINGER) {
    throw new HttpError(400, `You can have up to ${MAX_QUEUED_PER_SINGER} songs in line at once`);
  }

  await db.appendSong({
    singerId: session.sub,
    singerName: session.name,
    date,
    djId,
    song: {
      songId: crypto.randomUUID(),
      videoId,
      title,
      thumbnail,
      djId,
      status: 'queued',
      order: Date.now(),
      requestedAt: new Date().toISOString(),
    },
  });
  return json(200, await singerStatus(session.sub));
}

async function cancelSong(event) {
  const session = requireRole(event, 'singer');
  const songId = requireString(parseBody(event).songId, 'Song', 100);
  const date = tonight();
  const mine = await db.getNight(session.sub, date);
  const index = (mine?.songs ?? []).findIndex((s) => s.songId === songId);
  if (index < 0 || mine.songs[index].status !== 'queued') throw new HttpError(400, 'That song can no longer be removed');
  await db.updateSong({ singerId: session.sub, date, index, songId, set: { status: 'cancelled' } });
  return json(200, await singerStatus(session.sub));
}

async function tip(event) {
  const session = requireRole(event, 'singer');
  const date = tonight();
  const mine = await db.getNight(session.sub, date);
  if (!mine) throw new HttpError(400, 'Request a song before tipping');
  if (mine.tip) throw new HttpError(409, 'You already tipped tonight');

  const { queue } = buildQueue(await db.queryDjNight(mine.djId, date), mine.djId);
  let boost;
  try {
    boost = boostOrder(queue, session.sub);
  } catch (err) {
    throw new HttpError(400, err.message);
  }

  await db.recordTip({
    singerId: session.sub,
    date,
    djId: mine.djId,
    boost: boost && { index: boost.entry.index, songId: boost.entry.songId, order: boost.order },
  });
  return json(200, { boosted: Boolean(boost), ...(await singerStatus(session.sub)) });
}

async function history(event) {
  const session = requireRole(event, 'singer');
  let page;
  try {
    page = await db.historyPage(session.sub, event.queryStringParameters?.cursor);
  } catch (err) {
    if (err.message === 'Invalid history cursor') throw new HttpError(400, err.message);
    throw err;
  }
  const djNames = await db.getDjNames(page.items.map((i) => i.djId));
  return json(200, {
    cursor: page.cursor,
    nights: page.items.map((item) => ({
      date: item.date,
      requestId: item.requestId,
      djId: item.djId,
      djName: djNames[item.djId] ?? 'Unknown DJ',
      tipped: Boolean(item.tip),
      songs: (item.songs ?? []).map((s) => ({
        title: s.title,
        videoId: s.videoId,
        thumbnail: s.thumbnail,
        status: s.status,
        requestedAt: s.requestedAt,
        djName: djNames[s.djId] ?? djNames[item.djId],
      })),
    })),
  });
}

// ---- router ----------------------------------------------------------------

const routes = {
  'GET /auth/login': login,
  'GET /auth/callback': callback,
  'GET /auth/logout': logout,
  'GET /api/config': config,
  'GET /api/me': me,
  'GET /api/djs': listDjs,
  'POST /api/dj/profile': djProfile,
  'GET /api/dj/queue': djQueue,
  'POST /api/dj/next': djNext,
  'POST /api/dj/status': djSongStatus,
  'GET /api/singer/status': getStatus,
  'POST /api/singer/dj': chooseDj,
  'GET /api/youtube/search': youtubeSearch,
  'POST /api/singer/requests': requestSong,
  'POST /api/singer/requests/cancel': cancelSong,
  'POST /api/singer/tip': tip,
  'GET /api/singer/history': history,
};

export async function handler(event) {
  const method = event.requestContext.http.method;
  const route = routes[`${method} ${event.rawPath}`];
  if (!route) return json(404, { error: 'Not found' });

  if (method === 'POST' && !(event.headers?.['content-type'] ?? '').includes('application/json')) {
    return json(415, { error: 'Expected application/json' });
  }

  try {
    return await route(event);
  } catch (err) {
    if (err instanceof HttpError) return json(err.status, { error: err.message });
    if (err.name === 'ConditionalCheckFailedException') {
      return json(409, { error: 'The queue changed while you were looking. Refresh and try again.' });
    }
    console.error(err);
    return json(500, { error: 'Something went wrong' });
  }
}
