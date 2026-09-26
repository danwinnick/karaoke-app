import assert from 'node:assert/strict';
import { test } from 'node:test';
import { nightDate } from '../api/lib/night.mjs';
import { signToken, verifyToken } from '../api/lib/session.mjs';
import { cleanTitle } from '../api/lib/youtube.mjs';

test('night rolls over at 6am local time', () => {
  // 1am PDT Saturday -> Friday night
  assert.equal(nightDate(new Date('2026-06-06T08:00:00Z'), 'America/Los_Angeles'), '2026-06-05');
  // 7am PDT Saturday -> Saturday
  assert.equal(nightDate(new Date('2026-06-06T14:00:00Z'), 'America/Los_Angeles'), '2026-06-06');
  // 11pm PDT Friday -> Friday
  assert.equal(nightDate(new Date('2026-06-06T06:00:00Z'), 'America/Los_Angeles'), '2026-06-05');
});

test('session tokens round-trip and reject tampering', () => {
  const token = signToken({ sub: 'user_1', role: 'dj' }, 'secret', 60);
  assert.equal(verifyToken(token, 'secret').sub, 'user_1');
  assert.equal(verifyToken(token, 'other'), null);
  const [body, sig] = token.split('.');
  const forged = Buffer.from(JSON.stringify({ sub: 'user_2', role: 'dj', exp: 9e9 })).toString('base64url');
  assert.equal(verifyToken(`${forged}.${sig}`, 'secret'), null);
  assert.equal(verifyToken(`${body}.`, 'secret'), null);
  assert.equal(verifyToken(signToken({ sub: 'x' }, 'secret', -1), 'secret'), null);
});

test('cleanTitle strips karaoke noise', () => {
  assert.equal(cleanTitle('Adele - Someone Like You (Karaoke Version) | Sing King'), 'Adele - Someone Like You');
  assert.equal(cleanTitle('Queen - Bohemian Rhapsody [Karaoke with Lyrics]'), 'Queen - Bohemian Rhapsody');
  assert.equal(cleanTitle('Don&#39;t Stop Believin&#39; - Journey (Karaoke)'), "Don't Stop Believin' - Journey");
  assert.equal(cleanTitle('Karaoke - Toxic - Britney Spears'), 'Toxic - Britney Spears');
  assert.equal(cleanTitle('Wonderwall (Remastered)'), 'Wonderwall (Remastered)');
});
