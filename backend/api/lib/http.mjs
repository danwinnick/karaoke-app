export class HttpError extends Error {
  constructor(status, message) {
    super(message);
    this.status = status;
  }
}

export function json(status, data, cookies) {
  return {
    statusCode: status,
    headers: { 'content-type': 'application/json', 'cache-control': 'no-store' },
    ...(cookies ? { cookies } : {}),
    body: JSON.stringify(data),
  };
}

export function redirect(location, cookies) {
  return {
    statusCode: 302,
    headers: { location, 'cache-control': 'no-store' },
    ...(cookies ? { cookies } : {}),
  };
}

export function parseCookies(event) {
  const raw = event.cookies ?? (event.headers?.cookie ? event.headers.cookie.split(';') : []);
  const out = {};
  for (const c of raw) {
    const i = c.indexOf('=');
    if (i > 0) out[c.slice(0, i).trim()] = decodeURIComponent(c.slice(i + 1).trim());
  }
  return out;
}

export function setCookie(name, value, maxAgeSeconds) {
  return `${name}=${encodeURIComponent(value)}; Path=/; Max-Age=${maxAgeSeconds}; HttpOnly; Secure; SameSite=Lax`;
}

export function clearCookie(name) {
  return `${name}=; Path=/; Max-Age=0; HttpOnly; Secure; SameSite=Lax`;
}

export function parseBody(event) {
  if (!event.body) return {};
  const text = event.isBase64Encoded ? Buffer.from(event.body, 'base64').toString('utf8') : event.body;
  try {
    return JSON.parse(text);
  } catch {
    throw new HttpError(400, 'Invalid JSON body');
  }
}

export function requireString(value, field, max = 500) {
  if (typeof value !== 'string' || !value.trim()) throw new HttpError(400, `${field} is required`);
  if (value.length > max) throw new HttpError(400, `${field} is too long`);
  return value.trim();
}
