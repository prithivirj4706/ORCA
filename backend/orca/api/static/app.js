/* ORCA UI.
 *
 * Renders only what the API returned. Every number here came from a provenance
 * record, and anything the backend declared unavailable is drawn as visibly
 * absent with its reason -- never as an empty map, which would read as calm.
 *
 * The map is an ENHANCEMENT. Chat, the agent trace and the verdicts must never
 * wait on it or fail with it.
 */
(function () {
  'use strict';

  const API = location.origin;
  const $ = (s) => document.querySelector(s);
  const el = (tag, cls, html) => {
    const n = document.createElement(tag);
    if (cls) n.className = cls;
    if (html != null) n.innerHTML = html;
    return n;
  };
  const esc = (s) => String(s == null ? '' : s)
    .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
  const titleCase = (s) => String(s || '').replace(/_/g, ' ')
    .replace(/\b\w/g, (c) => c.toUpperCase());

  const VERDICT_COLOUR = {
    FAVOURABLE: 'var(--favourable)', PERMITTED: 'var(--favourable)',
    MARGINAL: 'var(--marginal)', RESTRICTED: 'var(--marginal)',
    UNFAVOURABLE: 'var(--unfavourable)',
    UNSAFE: 'var(--unsafe)', PROHIBITED: 'var(--unsafe)',
    INSUFFICIENT_EVIDENCE: 'var(--unknown)', UNKNOWN: 'var(--unknown)'
  };
  const BAND_COLOUR = {
    favourable: '#34d399', marginal: '#fbbf24',
    unfavourable: '#fb923c', unsafe: '#f43f5e'
  };
  const BAND_ORDER = ['favourable', 'marginal', 'unfavourable', 'unsafe'];

  const SUGGESTIONS = [
    'Is it good for fishing near Kochi tomorrow morning?',
    'Where is the nearest PFZ today?',
    'Safest route from Kochi to Chennai',
    'Am I inside the Indian EEZ?',
    'കൊച്ചിയിൽ നാളെ രാവിലെ മീൻപിടിക്കാൻ നല്ലതാണോ?'
  ];

  const ASK_HINT = {
    location: 'e.g. "near Kochi" or "9.93N 76.26E"',
    time_window: 'e.g. "tomorrow morning" or "tonight"',
    destination: 'e.g. "to Chennai"',
    intent: 'e.g. "is it safe?" or "how is the fishing?"'
  };

  /* Key-free tile hosts, in preference order. Esri's Ocean Base is first
   * because it is the right basemap for this product: it shades bathymetry,
   * so the sea reads as sea rather than as empty space. */
  const BASEMAPS = [
    { id: 'esri-ocean',
      url: 'https://services.arcgisonline.com/ArcGIS/rest/services/Ocean/World_Ocean_Base/MapServer/tile/{z}/{y}/{x}',
      attribution: 'Esri, GEBCO, NOAA · ORCA is not an official advisory',
      opacity: 0.92, saturation: -0.2 },
    { id: 'carto-dark',
      url: 'https://a.basemaps.cartocdn.com/dark_all/{z}/{x}/{y}.png',
      attribution: '© OpenStreetMap © CARTO · ORCA is not an official advisory',
      opacity: 0.55, saturation: -0.35 },
    { id: 'osm',
      url: 'https://tile.openstreetmap.org/{z}/{x}/{y}.png',
      attribution: '© OpenStreetMap contributors · ORCA is not an official advisory',
      opacity: 0.45, saturation: -0.6 }
  ];

  const FIELD_SPECS = [
    { name: 'wind', label: 'Wind', vector: true },
    { name: 'current', label: 'Currents', vector: true },
    { name: 'chlorophyll', label: 'Chlorophyll', vector: false,
      ramp: ['#082f49', '#0e7490', '#14b8a6', '#a3e635', '#fde047'] },
    { name: 'sst', label: 'Sea temp', vector: false,
      ramp: ['#1e3a8a', '#0ea5e9', '#fbbf24', '#f97316', '#dc2626'] },
    { name: 'waves', label: 'Waves', vector: false,
      ramp: ['#052e4a', '#0369a1', '#38bdf8', '#fbbf24', '#f43f5e'] }
  ];

  const NODE_LABEL = {
    ingest: 'Ingest', intent_context: 'Resolve intent, place and time',
    plan: 'Plan', tool_exec: 'Retrieve', validate: 'Validate evidence',
    replan: 'Re-plan', geo_reason: 'Align and derive',
    assess_safety: 'Assess safety', assess_fishing_suitability: 'Assess fishing',
    assess_regulatory: 'Assess regulatory', conflict_resolve: 'Resolve conflicts',
    evidence_assemble: 'Assemble evidence', review_gate: 'Review gate',
    human_review: 'Human review', report: 'Compose answer', finalize: 'Finalise',
    clarify: 'Ask for clarification', error_handler: 'Error'
  };

  let map, wind, thread = null, busy = false;
  let lastLocation = { lat: 9.93, lon: 76.26 };
  let basemapIndex = 0, basemapFailures = 0;
  const activeLayers = new Set();

  /* ------------------------------------------------------------------ map */

  function initMap() {
    // The initial style contains NO remote source. MapLibre holds a style
    // unloaded until its sources resolve, so a blocked or slow basemap would
    // stall style.load and nothing else would ever initialise. The basemap is
    // added afterwards as a cosmetic extra (16 section 4: hostile venue wifi).
    map = new maplibregl.Map({
      container: 'map',
      style: {
        version: 8, sources: {},
        layers: [{ id: 'bg', type: 'background',
                   paint: { 'background-color': '#050b14' } }]
      },
      center: [76.26, 9.93], zoom: 6.4, attributionControl: { compact: true }
    });
    map.addControl(new maplibregl.NavigationControl({ showCompass: false }),
                   'bottom-right');
    // Exposed at construction, not in a callback, so it is always inspectable.
    window.ORCA = { map, ask, layers: activeLayers };

    whenStyleReady(() => {
      wind = new window.OrcaWind.ParticleLayer($('#wind'), map);
      window.ORCA.wind = wind;
      addBasemap(0);
      loadBoundaries();
    });

    map.on('error', (e) => {
      if (e && e.error) console.warn('map:', e.error.message || e.error);
      try { noteTileFailure(e); } catch (_) { /* never fatal */ }
    });
  }

  /* Run map setup once the style can genuinely accept sources.
   *
   * A fixed timeout fallback was tried and was wrong: if the style was not
   * ready when it fired, addSource threw, the error was swallowed, and setup
   * was marked done for good, so the basemap and boundaries silently never
   * appeared. Poll for real readiness, and give up loudly. */
  function whenStyleReady(fn, deadlineMs) {
    const stop = Date.now() + (deadlineMs || 15000);
    let done = false;
    const attempt = () => {
      if (done) return;
      if (!map.isStyleLoaded()) {
        if (Date.now() > stop) {
          console.warn('map style never became ready; running without layers');
          return;
        }
        setTimeout(attempt, 250);
        return;
      }
      done = true;
      try { fn(); } catch (e) { console.warn('map setup failed:', e.message); }
    };
    map.once('style.load', attempt);
    attempt();
  }

  function addBasemap(i) {
    basemapIndex = i || 0;
    basemapFailures = 0;
    if (basemapIndex >= BASEMAPS.length) {
      console.warn('no basemap loaded; the sea is drawn without one');
      return;
    }
    const b = BASEMAPS[basemapIndex];
    try {
      if (map.getLayer('base')) map.removeLayer('base');
      if (map.getSource('base')) map.removeSource('base');
      map.addSource('base', {
        type: 'raster', tiles: [b.url], tileSize: 256, attribution: b.attribution
      });
      map.addLayer({
        id: 'base', type: 'raster', source: 'base',
        paint: { 'raster-opacity': b.opacity, 'raster-saturation': b.saturation }
      }, map.getLayer('eez-fill') ? 'eez-fill' : undefined);
    } catch (e) {
      console.warn('basemap ' + b.id + ' failed:', e.message);
      addBasemap(basemapIndex + 1);
    }
  }

  /* Tiles fail one at a time; several failures means this host is not serving
   * us, so move on rather than leave a half-drawn map. */
  function noteTileFailure(e) {
    const src = e && (e.sourceId || (e.source && e.source.id));
    if (src !== 'base') return;
    if (++basemapFailures === 4) addBasemap(basemapIndex + 1);
  }

  function loadBoundaries() {
    fetch(API + '/v1/boundaries?min_lat=2&min_lon=64&max_lat=26&max_lon=92')
      .then((r) => (r.ok ? r.json() : null))
      .then((gj) => {
        if (!gj || map.getSource('eez')) return;
        map.addSource('eez', { type: 'geojson', data: gj });
        map.addLayer({
          id: 'eez-fill', type: 'fill', source: 'eez',
          paint: { 'fill-color': '#4fd1c5', 'fill-opacity': 0.045 }
        });
        map.addLayer({
          id: 'eez-line', type: 'line', source: 'eez',
          paint: { 'line-color': '#4fd1c5', 'line-width': 1.1,
                   'line-opacity': 0.5, 'line-dasharray': [3, 2] }
        });
      })
      .catch(() => { /* boundaries are chrome; the verdict does not need them */ });
  }

  function fitTo(coords) {
    if (!coords || !coords.length) return;
    const b = coords.reduce((acc, c) => acc.extend(c),
      new maplibregl.LngLatBounds(coords[0], coords[0]));
    const wide = window.innerWidth > 1120;
    map.fitBounds(b, {
      padding: wide ? { top: 90, bottom: 120, left: 450, right: 420 }
                    : { top: 130, bottom: 160, left: 30, right: 30 },
      duration: 900
    });
  }

  /* --------------------------------------------------------------- fields */

  function buildLayerBar() {
    const bar = $('#layers');
    FIELD_SPECS.forEach((f) => {
      const b = el('button', 'lbtn', '<span class="ld"></span>' + f.label);
      b.dataset.field = f.name;
      b.onclick = () => toggleField(f, b);
      bar.appendChild(b);
    });
  }

  function toggleField(spec, btn) {
    if (!map || !map.isStyleLoaded()) {
      showFieldUnavailable(spec, 'the map is not ready yet');
      return;
    }
    if (activeLayers.has(spec.name)) {
      activeLayers.delete(spec.name);
      btn.classList.remove('on');
      if (spec.vector) { if (wind) wind.stop(); } else removeScalar(spec.name);
      if (!activeLayers.size) $('#legend').classList.remove('show');
      return;
    }
    // one vector field at a time -- two particle systems is noise
    if (spec.vector) {
      FIELD_SPECS.filter((f) => f.vector && activeLayers.has(f.name))
        .forEach((f) => {
          activeLayers.delete(f.name);
          const other = document.querySelector('.lbtn[data-field="' + f.name + '"]');
          if (other) other.classList.remove('on');
        });
    }
    btn.classList.add('loading');
    const radius = spec.vector ? 400 : 250;
    const url = API + '/v1/field/' + spec.name + '?lat=' + lastLocation.lat +
                '&lon=' + lastLocation.lon + '&radius_km=' + radius;
    fetch(url)
      .then((r) => (r.ok ? r.json()
                         : r.text().then((t) => Promise.reject(new Error(t)))))
      .then((data) => {
        activeLayers.add(spec.name);
        btn.classList.add('on');
        if (spec.vector) { if (wind) wind.set(data); } else drawScalar(spec, data);
        showLegend(spec, data);
      })
      .catch((e) => showFieldUnavailable(spec, e.message))
      .then(() => btn.classList.remove('loading'));
  }

  function showFieldUnavailable(spec, msg) {
    const lg = $('#legend');
    lg.classList.add('show');
    lg.innerHTML = '<h4>' + esc(spec.label) + '</h4>' +
      '<div style="color:var(--ink-faint);font-size:11px;line-height:1.5">' +
      'Not available for this area.<br>' +
      '<span style="font-family:var(--mono);font-size:10px">' +
      esc(String(msg).slice(0, 130)) + '</span></div>' +
      '<div class="cov">The layer is absent, not empty — an empty map would ' +
      'read as calm water.</div>';
  }

  /* Scalar fields rasterise to a canvas draped as an image source, so a null
   * cell is transparent: a hole you can see through, not a value. */
  function drawScalar(spec, data) {
    const rows = data.values, H = rows.length, W = rows[0].length;
    const cv = document.createElement('canvas');
    cv.width = W; cv.height = H;
    const ctx = cv.getContext('2d');
    const img = ctx.createImageData(W, H);
    const min = data.range.min, span = (data.range.max - data.range.min) || 1;

    for (let y = 0; y < H; y++) {
      for (let x = 0; x < W; x++) {
        const v = rows[y][x], i = (y * W + x) * 4;
        if (v === null) { img.data[i + 3] = 0; continue; }
        const c = rampAt(spec.ramp, (v - min) / span);
        img.data[i] = c[0]; img.data[i + 1] = c[1]; img.data[i + 2] = c[2];
        img.data[i + 3] = 205;
      }
    }
    ctx.putImageData(img, 0, 0);

    const latAsc = data.lats[0] < data.lats[data.lats.length - 1];
    const north = latAsc ? data.lats[data.lats.length - 1] : data.lats[0];
    const south = latAsc ? data.lats[0] : data.lats[data.lats.length - 1];
    const west = data.lons[0], east = data.lons[data.lons.length - 1];
    const url = latAsc ? flipVertical(cv) : cv.toDataURL();

    removeScalar(spec.name);
    map.addSource('f-' + spec.name, {
      type: 'image', url: url,
      coordinates: [[west, north], [east, north], [east, south], [west, south]]
    });
    map.addLayer({
      id: 'f-' + spec.name, type: 'raster', source: 'f-' + spec.name,
      paint: { 'raster-opacity': 0.72, 'raster-fade-duration': 350 }
    }, map.getLayer('eez-fill') ? 'eez-fill' : undefined);
  }

  function flipVertical(cv) {
    const out = document.createElement('canvas');
    out.width = cv.width; out.height = cv.height;
    const c = out.getContext('2d');
    c.translate(0, cv.height); c.scale(1, -1); c.drawImage(cv, 0, 0);
    return out.toDataURL();
  }

  function removeScalar(name) {
    if (map.getLayer('f-' + name)) map.removeLayer('f-' + name);
    if (map.getSource('f-' + name)) map.removeSource('f-' + name);
  }

  const hexToRgb = (h) => [1, 3, 5].map((i) => parseInt(h.slice(i, i + 2), 16));
  function rampAt(ramp, t) {
    t = Math.max(0, Math.min(1, t));
    const n = ramp.length - 1, i = Math.min(Math.floor(t * n), n - 1);
    const f = t * n - i, a = hexToRgb(ramp[i]), b = hexToRgb(ramp[i + 1]);
    return [0, 1, 2].map((k) => Math.round(a[k] + (b[k] - a[k]) * f));
  }

  function showLegend(spec, data) {
    const lg = $('#legend');
    lg.classList.add('show');
    const grad = spec.vector
      ? 'linear-gradient(90deg,#56d8d6,#38bdf8,#818cf8,#fbbf24,#f43f5e)'
      : 'linear-gradient(90deg,' + spec.ramp.join(',') + ')';
    const cov = Math.round((data.cells.coverage || 0) * 100);
    lg.innerHTML =
      '<h4>' + esc(data.label) + ' <span style="color:var(--ink-faint)">' +
      esc(data.unit) + '</span></h4>' +
      '<div class="ramp" style="background:' + grad + '"></div>' +
      '<div class="rlab"><span>' + data.range.min + '</span><span>' +
      data.range.max + '</span></div>' +
      '<div class="cov"><b style="color:' +
      (cov < 90 ? 'var(--amber)' : 'var(--ink-dim)') + '">' + cov +
      '% coverage</b> — ' + (data.cells.total - data.cells.valid) +
      ' cells masked, drawn as gaps.<br>' +
      '<span style="font-family:var(--mono);font-size:10px">' +
      esc(data.source) + ' · ' +
      esc(String(data.valid_time).slice(0, 16).replace('T', ' ')) + 'Z</span></div>';
  }

  /* ---------------------------------------------------------------- trace */

  function startTrace() {
    $('#right').classList.add('open');
    $('#rTitle').textContent = 'Agent trace';
    $('#rBody').innerHTML = '<div class="trace" id="traceList"></div>';
  }

  function pushTrace(ev) {
    const list = $('#traceList');
    if (!list) return;
    document.querySelectorAll('.tnode.live').forEach((n) => n.classList.remove('live'));
    if (list.children.length) list.appendChild(el('div', 'rail'));

    const bad = ev.status === 'error' || ev.status === 'failed';
    const warn = ev.status === 'degraded' || ev.status === 'partial';
    const row = el('div', 'tnode live');
    const name = ev.tool ? ev.tool : (NODE_LABEL[ev.node] || ev.node);
    row.innerHTML =
      '<span class="tdot ' + (bad ? 'err' : warn ? 'warn' : '') + '"></span>' +
      '<span class="tname">' + esc(name) + '</span>' +
      '<span class="tmeta">' + (ev.source ? esc(ev.source) + ' · ' : '') +
      (ev.duration_ms == null ? 0 : ev.duration_ms) + ' ms</span>';
    list.appendChild(row);

    const bits = [];
    if (ev.codes && ev.codes.length) bits.push(ev.codes.join(', '));
    if (ev.fallback_used) bits.push('served by a fallback');
    if (ev.summary && !ev.tool) bits.push(ev.summary);
    if (bits.length) list.appendChild(el('div', 'tsum', esc(bits.join(' — '))));
    list.parentElement.scrollTop = list.parentElement.scrollHeight;
  }

  /* --------------------------------------------------------------- answer */

  /* Band edges are not returned by the API, so a gauge is drawn only from what
   * IS returned: the driver's own band. The pin sits inside its band rather
   * than at a fabricated absolute position -- a made-up scale would be a
   * made-up fact. */
  function withBands(d) {
    const idx = BAND_ORDER.indexOf(d.band);
    d._bands = BAND_ORDER.map((b) => ({ band: b, w: 1 }));
    d._pin = idx < 0 ? 0.5 : (idx + 0.5) / BAND_ORDER.length;
    return d;
  }

  function gauge(driver, domain) {
    const val = driver.value;
    const limiting = driver.contribution === 'limiting';
    if (typeof val !== 'number') {
      // A boolean means CONTAINMENT in the regulatory domain and PRESENCE
      // elsewhere. "EEZ absent" reads as "there is no EEZ" rather than "you
      // are outside it", which is a different and wrong statement.
      const pair = domain === 'REGULATORY' ? ['inside', 'outside']
                                           : ['present', 'absent'];
      const shown = typeof val === 'boolean' ? (val ? pair[0] : pair[1])
                                             : (val == null ? '—' : val);
      return '<div class="grow"><span class="glabel' +
        (limiting ? ' limiting' : '') + '">' + (limiting ? '▸ ' : '') +
        esc(titleCase(driver.factor)) +
        '</span><span class="gval">' + esc(shown) + '</span></div>';
    }
    const segs = driver._bands.map((b) =>
      '<div class="gseg" style="flex:' + b.w + ';background:' +
      (BAND_COLOUR[b.band] || '#475569') + ';opacity:.55"></div>').join('');
    const pct = Math.max(0, Math.min(100, driver._pin * 100));
    return '<div class="gauge"><div class="grow"><span class="glabel' +
      (limiting ? ' limiting' : '') + '">' + (limiting ? '▸ ' : '') +
      esc(titleCase(driver.factor)) + '</span><span class="gval">' + val +
      (driver.unit ? ' ' + esc(driver.unit) : '') + '</span></div>' +
      '<div class="gbar">' + segs + '<div class="gpin" style="left:' + pct +
      '%"></div></div></div>';
  }

  function renderAnswer(d) {
    const out = $('#out');
    out.innerHTML = '';
    out.scrollTop = 0;
    $('#langBadge').textContent = (d.language || 'en').toUpperCase();
    const rec = d.recommendation || {};

    if (d.clarification_needed) {
      // ORCA is ASKING. The trace here is a handful of instant nodes and would
      // sit on top of the question, so get it out of the way and put the cursor
      // where the answer goes: a question the user cannot see is not a question.
      $('#right').classList.remove('open');
      out.appendChild(el('div', 'clarify',
        '<div class="qmark">?</div><div>' +
        '<div class="headline" style="margin:0 0 6px">' +
        esc(rec.headline || 'I need one more detail.') + '</div>' +
        '<div class="sub" style="margin:0">waiting on ' +
        esc(d.clarification_needed) + '</div></div>'));
      const q = $('#q');
      q.placeholder = ASK_HINT[d.clarification_needed] || 'Your answer…';
      q.value = '';
      q.focus();
      return;
    }
    $('#q').placeholder = 'Ask a follow-up…';

    out.appendChild(el('div', 'headline', esc(rec.headline || '')));
    const L = d.resolved_location;
    const where = L ? (L.label || '') + ' ' + (L.lat || 0).toFixed(2) + 'N ' +
                      (L.lon || 0).toFixed(2) + 'E' : '';
    out.appendChild(el('div', 'sub', esc(d.intent || '') + ' · ' + esc(where) +
      ' · ' + esc(String(d.disposition || '').toLowerCase())));

    (d.alerts || []).forEach((a) => {
      out.appendChild(el('div',
        'alert ' + (a.severity === 'warning' ? 'warning' : ''),
        '<i>' + (a.kind === 'inside' ? '◉' : '◈') + '</i><div><b>' +
        esc(titleCase(a.kind)) + ' ' + esc(titleCase(a.boundary_type)) + '</b>' +
        (a.name ? ' — ' + esc(a.name) : '') +
        (a.distance_km != null
          ? '<br><span style="font-family:var(--mono);font-size:10.5px;' +
            'color:var(--ink-faint)">' + a.distance_km + ' km · ' +
            esc(a.dataset_version || '') + ' · advisory only</span>'
          : '') + '</div>'));
    });

    if ((d.assessments || []).length) {
      out.appendChild(el('div', 'sec', 'Independent assessments'));
      const wrap = el('div', 'verdicts');
      d.assessments.forEach((a) => {
        const card = el('div', 'vcard');
        card.style.setProperty('--c', VERDICT_COLOUR[a.verdict] || 'var(--unknown)');
        const drivers = (a.drivers || []).map(withBands)
          .map((d) => gauge(d, a.domain)).join('');
        const gaps = (a.not_evaluated || []).map((n) => titleCase(n.factor)).slice(0, 6);
        card.innerHTML =
          '<div class="vtop"><span class="vdom">' + esc(titleCase(a.domain)) +
          '</span><span class="vverdict">' + esc(titleCase(a.verdict)) +
          '</span><span class="vconf">' + esc(a.confidence || '') + '</span></div>' +
          drivers +
          (a.rationale ? '<div class="rationale">' + esc(a.rationale) + '</div>' : '') +
          (gaps.length ? '<div class="gaps"><b>Not checked:</b> ' +
            esc(gaps.join(', ')) +
            (a.not_evaluated.length > 6 ? ' …' : '') + '</div>' : '');
        wrap.appendChild(card);
      });
      out.appendChild(wrap);
    }

    if ((d.evidence || []).length) {
      out.appendChild(el('div', 'sec', 'Evidence (' + d.evidence.length + ')'));
      d.evidence.slice(0, 12).forEach((e) => {
        const n = el('div', 'ev', esc(e.statement || e.parameter) +
          '<br><span class="p" title="Show the provenance chain">' +
          esc(e.provenance_id) + '</span>');
        n.querySelector('.p').onclick = () => showProvenance(e.provenance_id);
        out.appendChild(n);
      });
    }

    out.appendChild(el('div', 'disclaimer',
      'ORCA is not an official advisory service. It cites INCOIS and IMD; ' +
      'it does not replace them.'));

    if (L) lastLocation = L;
    // The map is drawn AFTER the answer and can never take it down: an
    // unloaded style used to throw here and replace the whole response.
    try {
      if (map && map.isStyleLoaded()) drawRunLayers(d);
      else if (map) map.once('style.load', () => {
        try { drawRunLayers(d); } catch (_) { /* cosmetic */ }
      });
    } catch (e) { console.warn('map layers skipped:', e.message); }
  }

  function drawRunLayers(d) {
    ['route-glow', 'route', 'here'].forEach((id) => {
      if (map.getLayer(id)) map.removeLayer(id);
    });
    ['route', 'here'].forEach((id) => {
      if (map.getSource(id)) map.removeSource(id);
    });

    const route = (d.map_layers || []).filter((l) => l.id === 'optimized_route')[0];
    if (route) {
      map.addSource('route', { type: 'geojson', data: route.data });
      map.addLayer({ id: 'route-glow', type: 'line', source: 'route',
        paint: { 'line-color': '#4fd1c5', 'line-width': 11,
                 'line-opacity': 0.14, 'line-blur': 8 } });
      map.addLayer({ id: 'route', type: 'line', source: 'route',
        layout: { 'line-cap': 'round', 'line-join': 'round' },
        paint: { 'line-color': '#7dd3fc', 'line-width': 2.6,
                 'line-dasharray': [0, 2.4, 3] } });
      animateDash();
      fitTo(route.data.geometry.coordinates);
    }

    const L = d.resolved_location;
    if (L && L.lat != null) {
      const pts = [{ type: 'Feature', properties: { k: 'origin' },
                     geometry: { type: 'Point', coordinates: [L.lon, L.lat] } }];
      if (L.dest_lat != null) {
        pts.push({ type: 'Feature', properties: { k: 'dest' },
                   geometry: { type: 'Point',
                               coordinates: [L.dest_lon, L.dest_lat] } });
      }
      map.addSource('here', { type: 'geojson',
        data: { type: 'FeatureCollection', features: pts } });
      map.addLayer({ id: 'here', type: 'circle', source: 'here',
        paint: { 'circle-radius': 6,
                 'circle-color': ['match', ['get', 'k'], 'dest', '#fbbf24', '#4fd1c5'],
                 'circle-stroke-width': 2,
                 'circle-stroke-color': 'rgba(255,255,255,.85)' } });
      if (!route) map.easeTo({ center: [L.lon, L.lat], zoom: 7.2, duration: 800 });
    }
  }

  let dashStep = 0, dashTimer = null;
  function animateDash() {
    const seq = [[0, 4, 3], [0.5, 4, 2.5], [1, 4, 2], [1.5, 4, 1.5],
                 [2, 4, 1], [2.5, 4, 0.5], [3, 4, 0]];
    clearInterval(dashTimer);
    dashTimer = setInterval(() => {
      if (!map.getLayer('route')) { clearInterval(dashTimer); return; }
      map.setPaintProperty('route', 'line-dasharray', seq[dashStep++ % seq.length]);
    }, 85);
  }

  function showProvenance(pid) {
    if (!thread) return;
    $('#right').classList.add('open');
    $('#rTitle').textContent = 'Provenance';
    $('#rBody').innerHTML = '<div class="empty"><span class="spin"></span></div>';
    fetch(API + '/v1/runs/' + thread + '/provenance?provenance_id=' +
          encodeURIComponent(pid))
      .then((r) => r.json())
      .then((d) => {
        const rec = (d.provenance || [])[0];
        if (!rec) { $('#rBody').innerHTML = '<div class="empty">No record.</div>'; return; }
        const row = (k, v) => (v == null || v === '') ? '' :
          '<div style="display:flex;gap:10px;padding:5px 0;' +
          'border-bottom:1px solid var(--line)">' +
          '<span style="color:var(--ink-faint);font-size:11px;min-width:118px">' +
          esc(k) + '</span><span style="font-family:var(--mono);font-size:11px;' +
          'flex:1;word-break:break-word">' + esc(v) + '</span></div>';
        const der = rec.derivation;
        $('#rBody').innerHTML =
          '<div class="sec" style="margin-top:0">Value</div>' +
          row('parameter', rec.parameter) + row('unit', rec.unit) +
          row('kind', rec.value_kind) +
          '<div class="sec">Source</div>' +
          row('source', rec.source) + row('source id', rec.source_id) +
          row('organisation', rec.organisation) + row('dataset', rec.dataset) +
          row('access', rec.access_method) +
          row('retrieved', String(rec.retrieved_at || '').slice(0, 19)) +
          (der ? '<div class="sec">Derivation</div>' +
            row('method', der.method + ' v' + der.method_version) +
            row('inputs', (der.inputs || []).join(', ')) +
            row('params', JSON.stringify(der.params || {})) : '') +
          (rec.licence_reference
            ? '<div class="disclaimer">' + esc(rec.licence_reference) + '</div>' : '');
      })
      .catch(() => {
        $('#rBody').innerHTML = '<div class="empty">Could not load provenance.</div>';
      });
  }

  /* ------------------------------------------------------------------ ask */

  function ask(text) {
    if (busy || !text || !text.trim()) return;
    busy = true;
    $('#go').disabled = true;
    $('#chips').style.display = 'none';   // the answer needs the room
    $('#out').innerHTML = '<div class="empty"><span class="spin"></span> Planning…</div>';
    startTrace();
    $('#q').value = '';

    const finish = () => {
      busy = false;
      $('#go').disabled = false;
      document.querySelectorAll('.tnode.live').forEach((n) => n.classList.remove('live'));
    };

    const body = JSON.stringify(thread ? { query: text, thread_id: thread }
                                       : { query: text });
    fetch(API + '/v1/chat/stream', {
      method: 'POST', headers: { 'Content-Type': 'application/json' }, body: body
    }).then((res) => {
      const reader = res.body.getReader();
      const dec = new TextDecoder();
      let buf = '';
      const handle = (chunk) => {
        const ev = /event: (\w+)/.exec(chunk);
        const dt = /data: ([\s\S]*)$/.exec(chunk);
        if (!ev || !dt) return;
        let payload;
        try { payload = JSON.parse(dt[1]); } catch (_) { return; }
        if (ev[1] === 'start') thread = payload.thread_id;
        else if (ev[1] === 'node') pushTrace(payload);
        else if (ev[1] === 'result') renderAnswer(payload);
        else if (ev[1] === 'error') {
          $('#out').innerHTML = '<div class="empty">' + esc(payload.error) + '</div>';
        }
      };
      const pump = () => reader.read().then((r) => {
        if (r.done) { if (buf.trim()) handle(buf); finish(); return; }
        buf += dec.decode(r.value, { stream: true });
        const parts = buf.split('\n\n');
        buf = parts.pop();
        parts.forEach(handle);
        return pump();
      });
      return pump();
    }).catch((e) => {
      $('#out').innerHTML = '<div class="empty">Request failed: ' +
        esc(e.message) + '</div>';
      finish();
    });
  }

  /* --------------------------------------------------------------- health */

  function loadHealth() {
    fetch(API + '/v1/health/sources').then((r) => r.json()).then((d) => {
      const on = d.sources.filter((s) => s.available).length;
      $('#healthTxt').textContent = on + '/' + d.sources.length + ' sources';
      if (on < d.sources.length) $('#health').querySelector('.dot').classList.add('warn');
      $('#health').onclick = () => {
        $('#right').classList.add('open');
        $('#rTitle').textContent = 'Source health';
        $('#rBody').innerHTML = d.sources.map((s) =>
          '<div style="display:flex;gap:10px;align-items:flex-start;padding:8px 0;' +
          'border-bottom:1px solid var(--line)">' +
          '<span class="dot" style="margin-top:6px;' +
          (s.available ? '' : 'background:var(--unknown);box-shadow:none') + '"></span>' +
          '<div style="flex:1"><div style="font-family:var(--mono);font-size:11.5px">' +
          esc(s.tool) + '</div>' +
          '<div style="font-size:11px;color:var(--ink-faint);line-height:1.45">' +
          esc(s.available ? s.description : (s.unavailable_reason || 'not bound')) +
          '</div></div></div>').join('') +
          '<div class="disclaimer">A capability with no source is declared, ' +
          'never hidden. Every answer names what it could not check.</div>';
      };
    }).catch(() => { $('#healthTxt').textContent = 'offline'; });
  }

  /* ----------------------------------------------------------------- boot */

  function boot() {
    initMap();
    buildLayerBar();
    loadHealth();

    const chips = $('#chips');
    SUGGESTIONS.forEach((s) => {
      const c = el('div', 'chip', esc(s.length > 42 ? s.slice(0, 40) + '…' : s));
      c.title = s;
      c.onclick = () => ask(s);
      chips.appendChild(c);
    });

    $('#go').onclick = () => ask($('#q').value);
    $('#q').addEventListener('keydown', (e) => {
      if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); ask($('#q').value); }
    });
    $('#q').addEventListener('input', function () {
      this.style.height = 'auto';
      this.style.height = Math.min(this.scrollHeight, 120) + 'px';
    });
    $('#rClose').onclick = () => $('#right').classList.remove('open');
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', boot);
  } else {
    boot();
  }
})();
