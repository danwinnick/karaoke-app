import { SecretsManagerClient, GetSecretValueCommand } from '@aws-sdk/client-secrets-manager';

const API = 'https://api.workos.com';
const secrets = new SecretsManagerClient({});

async function getApiKey() {
  const { SecretString } = await secrets.send(
    new GetSecretValueCommand({ SecretId: process.env.WORKOS_API_KEY_SECRET }),
  );
  const value = SecretString.trim();
  return value.startsWith('{') ? JSON.parse(value).api_key : value;
}

async function workos(apiKey, method, path, body) {
  const res = await fetch(`${API}${path}`, {
    method,
    headers: {
      authorization: `Bearer ${apiKey}`,
      'content-type': 'application/json',
    },
    body: body ? JSON.stringify(body) : undefined,
  });
  const text = await res.text();
  if (!res.ok) throw new Error(`WorkOS ${method} ${path} failed: ${res.status} ${text}`);
  return text ? JSON.parse(text) : {};
}

async function findByName(apiKey, path, name) {
  let after;
  do {
    const qs = new URLSearchParams({ limit: '100', ...(after ? { after } : {}) });
    const page = await workos(apiKey, 'GET', `${path}?${qs}`);
    const match = page.data.find((item) => item.name === name);
    if (match) return match;
    after = page.list_metadata?.after;
  } while (after);
  return null;
}

export async function handler(event) {
  const { organizationName, applicationName, redirectUri } = event;
  const apiKey = await getApiKey();

  const organization =
    (await findByName(apiKey, '/organizations', organizationName)) ??
    (await workos(apiKey, 'POST', '/organizations', { name: organizationName }));

  const redirectUris = [{ uri: redirectUri, default: true }];
  const existingApp = await findByName(apiKey, '/connect/applications', applicationName);
  const application = existingApp
    ? await workos(apiKey, 'PUT', `/connect/applications/${existingApp.id}`, {
        redirect_uris: redirectUris,
      })
    : await workos(apiKey, 'POST', '/connect/applications', {
        name: applicationName,
        description: 'Karaoke queue for DJs and singers',
        application_type: 'oauth',
        is_first_party: true,
        uses_pkce: true,
        redirect_uris: redirectUris,
      });

  return {
    organization_id: organization.id,
    application_id: application.id,
    client_id: application.client_id,
  };
}
