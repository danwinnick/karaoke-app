// Google Places autocomplete (Places API New) for a DJ's venue address. The picked place
// resolves to the fields the DJ table stores: address, lat, lng, and placeId.

let mapsLoaded = null;

function loadMaps(key) {
  mapsLoaded ??= new Promise((resolve, reject) => {
    window.karaokeMapsLoaded = resolve;
    const script = document.createElement('script');
    script.src = `https://maps.googleapis.com/maps/api/js?${new URLSearchParams({
      key,
      v: 'weekly',
      libraries: 'places',
      loading: 'async',
      callback: 'karaokeMapsLoaded',
    })}`;
    script.async = true;
    script.onerror = () => {
      mapsLoaded = null;
      reject(new Error('Google Maps failed to load'));
    };
    document.head.append(script);
  });
  return mapsLoaded;
}

// host: element the search box goes in; loading: its placeholder text; chosen: shows the pick.
export function placePicker({ host, loading, chosen }) {
  let place = null;
  let mounted = false;

  function show(next) {
    place = next;
    chosen.hidden = !place;
    chosen.textContent = place ? `📍 ${place.address}` : '';
  }

  async function onPlace(selected) {
    await selected.fetchFields({ fields: ['id', 'formattedAddress', 'location'] });
    show({
      address: selected.formattedAddress,
      lat: selected.location.lat(),
      lng: selected.location.lng(),
      placeId: selected.id,
    });
  }

  return {
    get place() {
      return place;
    },
    show,
    async mount(key) {
      if (mounted) return;
      mounted = true;
      try {
        await loadMaps(key);
      } catch {
        mounted = false;
        loading.textContent = 'Address search failed to load. Refresh to try again.';
        return;
      }
      const { PlaceAutocompleteElement } = await google.maps.importLibrary('places');
      const autocomplete = new PlaceAutocompleteElement();
      autocomplete.id = 'address';
      loading.remove();
      host.append(autocomplete);

      autocomplete.addEventListener('gmp-select', ({ placePrediction }) => onPlace(placePrediction.toPlace()));
      // Older versions of the element emit gmp-placeselect instead.
      autocomplete.addEventListener('gmp-placeselect', ({ place: selected }) => onPlace(selected));
    },
  };
}
