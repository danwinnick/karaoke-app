import { api, handleAuthError, homeFor } from './api.js';
import { placePicker } from './places.js';

const $ = (id) => document.getElementById(id);
const picker = placePicker({ host: $('address-host'), loading: $('address-loading'), chosen: $('address-chosen') });

$('dj-form').addEventListener('submit', async (e) => {
  e.preventDefault();
  const error = $('form-error');
  error.hidden = true;
  if (!picker.place) {
    error.textContent = 'Search for your address and pick it from the list.';
    error.hidden = false;
    return;
  }
  $('submit').disabled = true;
  try {
    await api('/api/dj/profile', {
      method: 'POST',
      body: { nickname: $('nickname').value, ...picker.place },
    });
    window.location.href = '/dj.html';
  } catch (err) {
    if (handleAuthError(err, 'dj')) return;
    error.textContent = err.message;
    error.hidden = false;
    $('submit').disabled = false;
  }
});

async function boot() {
  try {
    const [me, config] = await Promise.all([api('/api/me'), api('/api/config')]);
    if (me.role !== 'dj') {
      window.location.href = homeFor(me);
      return;
    }
    // DJs from before nicknames existed picked their public name as their "DJ name".
    $('nickname').value = me.dj?.nickname ?? me.dj?.name ?? '';
    $('email').value = me.user.email ?? '';
    if (me.dj) {
      $('heading').textContent = 'Edit your DJ profile';
      picker.show({ address: me.dj.address, lat: me.dj.lat, lng: me.dj.lng, placeId: me.dj.placeId });
    }
    picker.mount(config.googleMapsApiKey);
  } catch (err) {
    handleAuthError(err, 'dj');
  }
}

boot();
