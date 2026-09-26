import { api, handleAuthError } from './api.js';

const $ = (id) => document.getElementById(id);
let place = null;

function showPlace(next) {
  place = next;
  $('address-chosen').hidden = !place;
  $('address-chosen').textContent = place ? `📍 ${place.address}` : '';
}

async function onPlace(selected) {
  await selected.fetchFields({ fields: ['id', 'formattedAddress', 'location'] });
  showPlace({
    address: selected.formattedAddress,
    lat: selected.location.lat(),
    lng: selected.location.lng(),
    placeId: selected.id,
  });
}

// Google Places Autocomplete (Places API New).
window.karaokeInitPlaces = async () => {
  const { PlaceAutocompleteElement } = await google.maps.importLibrary('places');
  const autocomplete = new PlaceAutocompleteElement();
  autocomplete.id = 'address';
  $('address-loading').remove();
  $('address-host').append(autocomplete);

  autocomplete.addEventListener('gmp-select', ({ placePrediction }) => onPlace(placePrediction.toPlace()));
  // Older versions of the element emit gmp-placeselect instead.
  autocomplete.addEventListener('gmp-placeselect', ({ place: selected }) => onPlace(selected));
};

function loadMaps(key) {
  const script = document.createElement('script');
  script.src = `https://maps.googleapis.com/maps/api/js?${new URLSearchParams({
    key,
    v: 'weekly',
    libraries: 'places',
    loading: 'async',
    callback: 'karaokeInitPlaces',
  })}`;
  script.async = true;
  script.onerror = () => {
    $('address-loading').textContent = 'Address search failed to load. Refresh to try again.';
  };
  document.head.append(script);
}

$('dj-form').addEventListener('submit', async (e) => {
  e.preventDefault();
  const error = $('form-error');
  error.hidden = true;
  if (!place) {
    error.textContent = 'Search for your address and pick it from the list.';
    error.hidden = false;
    return;
  }
  $('submit').disabled = true;
  try {
    await api('/api/dj/profile', {
      method: 'POST',
      body: { name: $('name').value, email: $('email').value, ...place },
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
      window.location.href = '/auth/login?role=dj';
      return;
    }
    $('name').value = me.dj?.name ?? me.user.name ?? '';
    $('email').value = me.dj?.email ?? me.user.email ?? '';
    if (me.dj) {
      $('heading').textContent = 'Edit your DJ profile';
      showPlace({ address: me.dj.address, lat: me.dj.lat, lng: me.dj.lng, placeId: me.dj.placeId });
    }
    loadMaps(config.googleMapsApiKey);
  } catch (err) {
    handleAuthError(err, 'dj');
  }
}

boot();
