import crypto from 'node:crypto';
import { SecretsManagerClient, GetSecretValueCommand } from '@aws-sdk/client-secrets-manager';

const API = 'https://api.workos.com';
const secrets = new SecretsManagerClient({});
let apiKeyPromise;
let oidcPromise;

export function getApiKey() {
  apiKeyPromise ??= secrets
    .send(new GetSecretValueCommand({ SecretId: process.env.WORKOS_API_KEY_SECRET }))
    .then(({ SecretString }) => {
      const value = SecretString.trim();
      return value.startsWith('{') ? JSON.parse(value).api_key : value;
    })
    .catch((err) => {
      apiKeyPromise = undefined;
      throw err;
    });
  return apiKeyPromise;
}

// AuthKit acts as the OAuth/OIDC provider for the first-party Connect application
// created by the karaoke-workos-bootstrap Lambda.
function oidcConfig() {
  const issuer = `https://${process.env.AUTHKIT_DOMAIN}`;
  oidcPromise ??= fetch(`${issuer}/.well-known/openid-configuration`)
    .then((res) => (res.ok ? res.json() : {}))
    .catch(() => ({}))
    .then((cfg) => ({
      authorization_endpoint: cfg.authorization_endpoint ?? `${issuer}/oauth2/authorize`,
      token_endpoint: cfg.token_endpoint ?? `${issuer}/oauth2/token`,
    }));
  return oidcPromise;
}

export function pkcePair() {
  const verifier = crypto.randomBytes(32).toString('base64url');
  const challenge = crypto.createHash('sha256').update(verifier).digest('base64url');
  return { verifier, challenge };
}

export async function authorizeUrl({ state, challenge, redirectUri, signup }) {
  const { authorization_endpoint } = await oidcConfig();
  const params = new URLSearchParams({
    response_type: 'code',
    client_id: process.env.WORKOS_CLIENT_ID,
    redirect_uri: redirectUri,
    scope: 'openid profile email',
    state,
    code_challenge: challenge,
    code_challenge_method: 'S256',
  });
  if (signup) params.set('screen_hint', 'sign-up');
  return `${authorization_endpoint}?${params}`;
}

export async function exchangeCode({ code, verifier, redirectUri }) {
  const { token_endpoint } = await oidcConfig();
  const res = await fetch(token_endpoint, {
    method: 'POST',
    headers: { 'content-type': 'application/x-www-form-urlencoded' },
    body: new URLSearchParams({
      grant_type: 'authorization_code',
      code,
      redirect_uri: redirectUri,
      client_id: process.env.WORKOS_CLIENT_ID,
      code_verifier: verifier,
    }),
  });
  if (!res.ok) throw new Error(`Token exchange failed: ${res.status} ${await res.text()}`);
  return res.json();
}

// The id_token comes straight from the token endpoint over TLS, so its claims can be
// trusted without re-verifying the signature (OIDC Core 3.1.3.7).
export function decodeJwt(token) {
  const [, payload] = token.split('.');
  return JSON.parse(Buffer.from(payload, 'base64url').toString('utf8'));
}

async function workos(method, path, body) {
  const res = await fetch(`${API}${path}`, {
    method,
    headers: { authorization: `Bearer ${await getApiKey()}`, 'content-type': 'application/json' },
    body: body ? JSON.stringify(body) : undefined,
  });
  const text = await res.text();
  if (!res.ok) {
    const err = new Error(`WorkOS ${method} ${path} failed: ${res.status} ${text}`);
    err.status = res.status;
    throw err;
  }
  return text ? JSON.parse(text) : {};
}

export function getUser(userId) {
  return workos('GET', `/user_management/users/${encodeURIComponent(userId)}`);
}

// Everyone who signs in joins the deployment's organization. Already-a-member errors are ignored.
export async function ensureMembership(userId) {
  try {
    await workos('POST', '/user_management/organization_memberships', {
      user_id: userId,
      organization_id: process.env.WORKOS_ORG_ID,
    });
  } catch (err) {
    if (!(err.status >= 400 && err.status < 500)) throw err;
  }
}
