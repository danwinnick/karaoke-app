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
// Names from the signup form, sent again with the code so the account is created once it's verified.
let names = {};

function showError(message) {
  $('dj-error').textContent = message;
  $('dj-error').hidden = !message;
}

function showForm(id) {
  for (const form of ['dj-email-form', 'dj-signup-form', 'dj-code-form']) $(form).hidden = form !== id;
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

function showCodeForm() {
  $('dj-code-sent').textContent = `We emailed a code to ${email}. It expires in 10 minutes.`;
  showForm('dj-code-form');
  $('dj-code').value = '';
  $('dj-code').focus();
}

$('dj-email-form').addEventListener('submit', (e) => {
  e.preventDefault();
  submit($('dj-email-submit'), async () => {
    const value = $('dj-email').value.trim();
    const res = await api('/auth/dj/code', { method: 'POST', body: { email: value } });
    email = value;
    names = {};
    if (res.signup) {
      $('dj-signup-intro').textContent = `There's no DJ account for ${email} yet. Sign up to get started.`;
      showForm('dj-signup-form');
      $('dj-first-name').focus();
      return;
    }
    showCodeForm();
  });
});

$('dj-signup-form').addEventListener('submit', (e) => {
  e.preventDefault();
  submit($('dj-signup-submit'), async () => {
    const next = { firstName: $('dj-first-name').value.trim(), lastName: $('dj-last-name').value.trim() };
    if (!next.firstName) throw new ApiError(400, 'Enter your first name');
    await api('/auth/dj/code', { method: 'POST', body: { email, ...next } });
    names = next;
    showCodeForm();
  });
});

$('dj-code-form').addEventListener('submit', (e) => {
  e.preventDefault();
  submit($('dj-code-submit'), async () => {
    const res = await api('/auth/dj/verify', { method: 'POST', body: { email, code: $('dj-code').value.trim(), ...names } });
    window.location.href = res.redirect;
  });
});

for (const id of ['dj-code-back', 'dj-signup-back']) {
  $(id).addEventListener('click', () => {
    showError('');
    showForm('dj-email-form');
    $('dj-email').focus();
  });
}
