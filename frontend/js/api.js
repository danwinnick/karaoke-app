// CloudFront signs requests to the Lambda origin with SigV4, which requires the
// payload hash from the viewer on requests that carry a body.
async function sha256Hex(text) {
  const digest = await crypto.subtle.digest('SHA-256', new TextEncoder().encode(text));
  return [...new Uint8Array(digest)].map((b) => b.toString(16).padStart(2, '0')).join('');
}

export class ApiError extends Error {
  constructor(status, message) {
    super(message);
    this.status = status;
  }
}

export async function api(path, { method = 'GET', body } = {}) {
  const init = { method, credentials: 'same-origin', headers: {} };
  if (method !== 'GET') {
    const text = JSON.stringify(body ?? {});
    init.body = text;
    init.headers['content-type'] = 'application/json';
    init.headers['x-amz-content-sha256'] = await sha256Hex(text);
  }
  const res = await fetch(path, init);
  const data = await res.json().catch(() => ({}));
  if (!res.ok) throw new ApiError(res.status, data.error ?? res.statusText);
  return data;
}

// Where a logged-in user belongs. Each account has exactly one role, fixed at first login.
export function homeFor(me) {
  if (me.role === 'dj') return me.dj ? '/dj.html' : '/dj-signup.html';
  return me.singer ? '/singer.html' : '/singer-signup.html';
}

// Sends logged-out users to log in for this page's role. Anyone else (wrong role, or
// signup not finished) goes to the landing page, which routes them to their own page.
export function handleAuthError(err, role) {
  if (err instanceof ApiError && err.status === 401) {
    window.location.href = role === 'dj' ? '/?role=dj' : '/auth/login';
    return true;
  }
  if (err instanceof ApiError && err.status === 403) {
    window.location.href = '/';
    return true;
  }
  return false;
}

export function el(tag, attrs = {}, ...children) {
  const node = document.createElement(tag);
  for (const [key, value] of Object.entries(attrs)) {
    if (value === undefined || value === null || value === false) continue;
    if (key.startsWith('on')) node.addEventListener(key.slice(2), value);
    else if (key === 'class') node.className = value;
    else node.setAttribute(key, value === true ? '' : value);
  }
  for (const child of children.flat()) {
    if (child === null || child === undefined || child === false) continue;
    node.append(child instanceof Node ? child : document.createTextNode(String(child)));
  }
  return node;
}

export function toast(message, { kind = 'info', timeout = 3500 } = {}) {
  let host = document.getElementById('toasts');
  if (!host) {
    host = el('div', { id: 'toasts', 'aria-live': 'polite' });
    document.body.append(host);
  }
  const node = el('div', { class: `toast toast-${kind}` }, message);
  host.append(node);
  setTimeout(() => node.remove(), timeout);
}

export function formatDate(isoDate) {
  const [y, m, d] = isoDate.split('-').map(Number);
  return new Date(y, m - 1, d).toLocaleDateString(undefined, {
    weekday: 'short',
    month: 'short',
    day: 'numeric',
    year: 'numeric',
  });
}
