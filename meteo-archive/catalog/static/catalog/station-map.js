(() => {
  const canvas = document.getElementById('station-map');
  if (!canvas || !window.maplibregl) return;
  const status = document.querySelector('[data-map-status]');

  const localStyle = {
    version: 8,
    sources: {},
    layers: [{ id: 'background', type: 'background', paint: { 'background-color': '#eaf2eb' } }],
  };

  function showMap(collection) {
    if (!collection.features.length) {
      status.textContent = 'В этой выборке ещё нет станций с подтверждёнными координатами.';
      return;
    }
    canvas.hidden = false;
    const map = new maplibregl.Map({
      container: canvas,
      style: canvas.dataset.mapStyle || localStyle,
      center: collection.features[0].geometry.coordinates,
      zoom: 12,
      maxZoom: 16,
    });
    map.addControl(new maplibregl.NavigationControl({ showCompass: false }));
    map.addControl(new maplibregl.ScaleControl({ unit: 'metric' }));
    map.once('load', () => {
      map.addSource('station-points', { type: 'geojson', data: collection });
      map.addLayer({
        id: 'station-points',
        type: 'circle',
        source: 'station-points',
        paint: { 'circle-color': '#0d6652', 'circle-radius': 9, 'circle-stroke-color': '#ffffff', 'circle-stroke-width': 2 },
      });
      if (collection.features.length > 1) {
        const bounds = new maplibregl.LngLatBounds();
        collection.features.forEach(feature => bounds.extend(feature.geometry.coordinates));
        map.fitBounds(bounds, { padding: 52, maxZoom: 14, duration: 0 });
      }
      map.on('mouseenter', 'station-points', () => { map.getCanvas().style.cursor = 'pointer'; });
      map.on('mouseleave', 'station-points', () => { map.getCanvas().style.cursor = ''; });
      map.on('click', 'station-points', event => {
        const feature = event.features?.[0];
        if (!feature) return;
        const popup = document.createElement('div');
        const label = document.createElement('strong');
        label.textContent = feature.properties.name;
        const link = document.createElement('a');
        link.href = feature.properties.station_url;
        link.textContent = 'Открыть станцию';
        popup.append(label, document.createElement('br'), link);
        new maplibregl.Popup().setLngLat(feature.geometry.coordinates).setDOMContent(popup).addTo(map);
      });
      status.textContent = 'Нажмите на точку, чтобы открыть карточку станции.';
    });
  }

  fetch(canvas.dataset.stationSource, { headers: { Accept: 'application/geo+json' } })
    .then(response => {
      if (!response.ok) throw new Error('Station source unavailable');
      return response.json();
    })
    .then(showMap)
    .catch(() => {
      canvas.hidden = true;
      status.textContent = 'Карта недоступна. Карточки станций можно открыть в таблице.';
    });
})();
