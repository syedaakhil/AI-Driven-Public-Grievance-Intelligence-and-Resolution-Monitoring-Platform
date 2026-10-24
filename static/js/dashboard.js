/* City Corporation — Home dashboard: charts, trend toggle and the ward map.
   All numbers come from the server (json_script). Chart.js loads from a CDN;
   without internet a short message replaces each chart. */
(function () {
  'use strict';

  // Translations from Django's JavaScript catalogue (/jsi18n/); English if it didn't load.
  var gettext = window.gettext || function (text) { return text; };
  var interpolate = window.interpolate || function (text, values) {
    return text.replace(/%\((\w+)\)s/g, function (_, key) { return values[key]; });
  };

  var dataElement = document.getElementById('dashboard-data');
  if (!dataElement) return;
  var data = JSON.parse(dataElement.textContent);
  var charts = {};

  /* ---------- Charts ---------- */
  function showFallback(canvasId) {
    var canvas = document.getElementById(canvasId);
    if (!canvas) return;
    var note = document.createElement('div');
    note.className = 'chart-fallback';
    note.textContent = gettext('Chart could not load (Chart.js needs an internet connection). The figures above still work.');
    canvas.parentElement.appendChild(note);
  }

  function buildCharts() {
    var Chart = window.Chart;
    var animation = document.documentElement.classList.contains('reduce-motion') ? false : { duration: 500 };
    var number = new Intl.NumberFormat('en-IN');

    Chart.defaults.font.family = getComputedStyle(document.body).fontFamily;
    Chart.defaults.font.size = 10;
    Chart.defaults.color = '#7A8798';
    Chart.defaults.plugins.legend.display = false;
    Chart.defaults.plugins.tooltip.backgroundColor = '#263B5B';
    Chart.defaults.plugins.tooltip.padding = 10;
    Chart.defaults.plugins.tooltip.cornerRadius = 6;

    var bar = { borderRadius: 2, barPercentage: 0.72, categoryPercentage: 0.55 };
    charts.trend = new Chart(document.getElementById('trend-chart'), {
      type: 'bar',
      data: {
        labels: data.monthly.labels,
        datasets: [
          Object.assign({ label: gettext('Reported'), data: data.monthly.reported, backgroundColor: '#2E5487' }, bar),
          Object.assign({ label: gettext('Resolved'), data: data.monthly.resolved, backgroundColor: '#A9C1DE' }, bar)
        ]
      },
      options: {
        maintainAspectRatio: false,
        animation: animation,
        interaction: { mode: 'index', intersect: false },
        scales: {
          x: { grid: { display: false }, border: { color: '#E7EAF0' } },
          y: {
            beginAtZero: true, suggestedMax: 4, border: { display: false },
            grid: { color: '#EEF1F5' }, ticks: { precision: 0, maxTicksLimit: 5 }
          }
        },
        plugins: {
          tooltip: { callbacks: { label: function (item) { return ' ' + item.dataset.label + ': ' + number.format(item.raw); } } }
        }
      }
    });

    var channels = data.channels;
    var noData = channels.counts.length === 0;
    charts.channels = new Chart(document.getElementById('channel-chart'), {
      type: 'doughnut',
      data: {
        labels: noData ? [gettext('No complaints yet')] : channels.labels,
        datasets: [{
          data: noData ? [1] : channels.counts,
          backgroundColor: noData ? ['#EEF1F5'] : channels.colours,
          borderColor: '#fff', borderWidth: 3, hoverOffset: noData ? 0 : 4
        }]
      },
      options: {
        maintainAspectRatio: false,
        cutout: '72%',
        animation: animation,
        plugins: {
          tooltip: {
            enabled: !noData,
            callbacks: {
              label: function (item) {
                var total = item.dataset.data.reduce(function (sum, value) { return sum + value; }, 0);
                return ' ' + number.format(item.raw) + ' (' + Math.round(item.raw / total * 100) + '%)';
              }
            }
          }
        }
      }
    });

    var lineCanvas = document.getElementById('resolved-chart');
    var fill = lineCanvas.getContext('2d').createLinearGradient(0, 0, 0, 160);
    fill.addColorStop(0, 'rgba(46, 84, 135, .22)');
    fill.addColorStop(1, 'rgba(46, 84, 135, 0)');
    charts.resolved = new Chart(lineCanvas, {
      type: 'line',
      data: {
        labels: data.resolvedLine.labels,
        datasets: [{
          label: gettext('Resolved'), data: data.resolvedLine.values,
          borderColor: '#2E5487', borderWidth: 2, backgroundColor: fill, fill: true,
          tension: 0.42, pointRadius: 0, pointHoverRadius: 4, pointBackgroundColor: '#2E5487'
        }]
      },
      options: {
        maintainAspectRatio: false,
        animation: animation,
        interaction: { mode: 'index', intersect: false },
        scales: {
          x: { grid: { display: false }, border: { display: false } },
          y: { display: false, beginAtZero: true, suggestedMax: 3 }
        },
        plugins: {
          tooltip: { callbacks: { label: function (item) { return ' ' + number.format(item.raw) + ' resolved'; } } }
        }
      }
    });
  }

  if (window.Chart) {
    buildCharts();
  } else {
    ['trend-chart', 'channel-chart', 'resolved-chart'].forEach(showFallback);
  }

  /* ---------- Monthly / weekly toggle ---------- */
  document.querySelectorAll('[data-trend]').forEach(function (button) {
    button.addEventListener('click', function () {
      document.querySelectorAll('[data-trend]').forEach(function (other) {
        other.setAttribute('aria-pressed', String(other === button));
      });
      if (!charts.trend) return;
      var series = data[button.getAttribute('data-trend')];
      charts.trend.data.labels = series.labels;
      charts.trend.data.datasets[0].data = series.reported;
      charts.trend.data.datasets[1].data = series.resolved;
      charts.trend.update();
    });
  });

  /* ---------- Ward map: highlight a ward on the map and in the list together ---------- */
  var wardMap = document.querySelector('.wardmap');
  function highlight(id) {
    document.querySelectorAll('.wardmap [data-ward]').forEach(function (el) {
      el.classList.toggle('is-hot', id !== null && el.getAttribute('data-ward') === id);
    });
  }
  if (wardMap) {
    wardMap.addEventListener('mouseover', function (event) {
      var target = event.target.closest('[data-ward]');
      highlight(target ? target.getAttribute('data-ward') : null);
    });
    wardMap.addEventListener('mouseleave', function () { highlight(null); });
  }

  /* ---------- Optional street map (Leaflet + OpenStreetMap), loaded on demand ---------- */
  var pointsElement = document.getElementById('ward-points');
  var points = pointsElement ? JSON.parse(pointsElement.textContent) : [];
  var street = document.getElementById('street-map');
  var caption = document.getElementById('map-caption');
  var streetMap = null;

  function loadLeaflet(done) {
    if (window.L) { done(true); return; }
    var finished = false;
    function finish(ok) { if (!finished) { finished = true; done(ok); } }
    var css = document.createElement('link');
    css.rel = 'stylesheet';
    css.href = 'https://unpkg.com/leaflet@1.9.4/dist/leaflet.css';
    document.head.appendChild(css);
    var script = document.createElement('script');
    script.src = 'https://unpkg.com/leaflet@1.9.4/dist/leaflet.js';
    script.onload = function () { finish(!!window.L); };
    script.onerror = function () { finish(false); };
    setTimeout(function () { finish(false); }, 8000);
    document.head.appendChild(script);
  }

  function popup(point) {
    var box = document.createElement('div');
    var title = document.createElement('strong');
    title.textContent = point.label;
    var info = document.createElement('div');
    info.textContent = interpolate(gettext('%(area)s · %(count)s open'), { area: point.area, count: point.open }, true);
    var link = document.createElement('a');
    link.href = street.getAttribute('data-complaints-url') + '?ward=' + encodeURIComponent(point.id);
    link.textContent = gettext('View complaints');
    box.append(title, info, link);
    return box;
  }

  function setMapMode(mode) {
    document.querySelectorAll('[data-map]').forEach(function (button) {
      button.setAttribute('aria-pressed', String(button.getAttribute('data-map') === mode));
    });
    if (mode === 'illustrated') {
      street.hidden = true;
      caption.textContent = gettext('Illustrative ward boundaries · not to scale');
      return;
    }
    street.hidden = false;
    if (streetMap) {
      streetMap.invalidateSize();
      caption.textContent = gettext('Chennai, Tamil Nadu · © OpenStreetMap contributors');
      return;
    }
    caption.textContent = gettext('Loading street map…');
    loadLeaflet(function (ok) {
      if (!ok) {
        setMapMode('illustrated');
        caption.textContent = gettext('Street map unavailable offline — showing the illustrated map.');
        return;
      }
      var L = window.L;
      streetMap = L.map(street, { scrollWheelZoom: false }).setView([13.045, 80.245], 11);
      L.tileLayer('https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png', {
        maxZoom: 18,
        attribution: '&copy; OpenStreetMap contributors'
      }).addTo(streetMap);
      points.forEach(function (point) {
        L.circleMarker([point.lat, point.lng], {
          radius: 6 + Math.min(point.open, 24), color: '#2E5487', weight: 1.5, fillColor: '#2E5487', fillOpacity: 0.35
        }).addTo(streetMap).bindPopup(popup(point));
      });
      caption.textContent = gettext('Chennai, Tamil Nadu · © OpenStreetMap contributors');
      setTimeout(function () { streetMap.invalidateSize(); }, 50);
    });
  }

  if (street) {
    document.querySelectorAll('[data-map]').forEach(function (button) {
      button.addEventListener('click', function () { setMapMode(button.getAttribute('data-map')); });
    });
  }
})();
