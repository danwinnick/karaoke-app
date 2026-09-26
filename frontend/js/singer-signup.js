import { api, handleAuthError } from './api.js';

const $ = (id) => document.getElementById(id);

$('singer-form').addEventListener('submit', async (e) => {
  e.preventDefault();
  const error = $('form-error');
  error.hidden = true;
  $('submit').disabled = true;
  try {
    await api('/api/singer/profile', {
      method: 'POST',
      body: { name: $('name').value, email: $('email').value },
    });
    window.location.href = '/singer.html';
  } catch (err) {
    if (handleAuthError(err, 'singer')) return;
    error.textContent = err.message;
    error.hidden = false;
    $('submit').disabled = false;
  }
});

async function boot() {
  try {
    const me = await api('/api/me');
    if (me.role !== 'singer') {
      window.location.href = '/auth/login?role=singer';
      return;
    }
    $('name').value = me.singer?.name ?? me.user.name ?? '';
    $('email').value = me.singer?.email ?? me.user.email ?? '';
    if (me.singer) $('heading').textContent = 'Edit your singer profile';
  } catch (err) {
    handleAuthError(err, 'singer');
  }
}

boot();
