import { api, ApiError, homeFor } from './api.js';

const $ = (id) => document.getElementById(id);
const params = new URLSearchParams(location.search);

if (params.get('error')) {
  $('error').textContent = params.get('error');
  $('error').hidden = false;
}

function showRole(role) {
  $('choose-role').hidden = Boolean(role);
  $('role-singer').hidden = role !== 'singer';
  $('role-dj').hidden = role !== 'dj';
}
document.querySelectorAll('[data-role]').forEach((button) => {
  button.addEventListener('click', () => showRole(button.dataset.role));
});
showRole(params.get('role') === 'dj' ? 'dj' : '');

// Logged-in users can't pick a role; they go straight to their own page.
api('/api/me')
  .then((me) => window.location.replace(homeFor(me)))
  .catch(() => {});

// ---- DJ email code login ----------------------------------------------------

let email = '';

function showError(message) {
  $('dj-error').textContent = message;
  $('dj-error').hidden = !message;
}

async function submit(button, action) {
  showError('');
  button.disabled = true;
  try {
    await action();
  } catch (err) {
    showError(err instanceof ApiError ? err.message : 'Something went wrong. Try again.');
  } finally {
    button.disabled = false;
  }
}

$('dj-email-form').addEventListener('submit', (e) => {
  e.preventDefault();
  submit($('dj-email-submit'), async () => {
    const value = $('dj-email').value.trim();
    await api('/auth/dj/code', { method: 'POST', body: { email: value } });
    email = value;
    $('dj-code-sent').textContent = `We emailed a code to ${email}. It expires in 10 minutes.`;
    $('dj-email-form').hidden = true;
    $('dj-code-form').hidden = false;
    $('dj-code').value = '';
    $('dj-code').focus();
  });
});

$('dj-code-form').addEventListener('submit', (e) => {
  e.preventDefault();
  submit($('dj-code-submit'), async () => {
    const res = await api('/auth/dj/verify', { method: 'POST', body: { email, code: $('dj-code').value.trim() } });
    window.location.href = res.redirect;
  });
});

$('dj-code-back').addEventListener('click', () => {
  showError('');
  $('dj-code-form').hidden = true;
  $('dj-email-form').hidden = false;
  $('dj-email').focus();
});
