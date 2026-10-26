/* City Corporation — Agent Work page: the "Collect data" button on each dataset card.
   POSTs to /api/agent-work/collect/, then polls the job's status URL until it finishes.
   Server text is inserted with textContent, never as HTML. buttonState() is pure, so
   mlops/tests_js/agent-work.test.js can check every button state with Node. */
(function (root, factory) {
  'use strict';
  var api = factory(root);
  if (typeof module === 'object' && module.exports) module.exports = api;
  else root.AgentWork = api;
  if (typeof document !== 'undefined') api.init(document);
}(typeof window !== 'undefined' ? window : this, function (root) {
  'use strict';

  var gettext = (root && root.gettext) || function (text) { return text; };
  var POLL_MS = 1500;
  var MAX_POLL_FAILURES = 5;

  /* What the button shows for a job (or none) and the request state.
     opts: {requesting: bool, requestError: bool, disabled: bool}
     Returns {state, label, disabled, busy}; state is idle | loading | success | error | disabled. */
  function buttonState(job, opts) {
    opts = opts || {};
    if (opts.disabled) return { state: 'disabled', label: gettext('Collect data'), disabled: true, busy: false };
    if (opts.requesting) return { state: 'loading', label: gettext('Starting…'), disabled: true, busy: true };
    if (opts.requestError) return { state: 'error', label: gettext('Try again'), disabled: false, busy: false };
    if (!job) return { state: 'idle', label: gettext('Collect data'), disabled: false, busy: false };
    if (job.status === 'queued') return { state: 'loading', label: gettext('Starting…'), disabled: true, busy: true };
    if (job.status === 'running') return { state: 'loading', label: gettext('Collecting…'), disabled: true, busy: true };
    if (job.status === 'succeeded') return { state: 'success', label: gettext('Collect again'), disabled: false, busy: false };
    return { state: 'error', label: gettext('Try again'), disabled: false, busy: false };
  }

  function badgeFor(job) {
    if (!job) return { cls: 'badge--plain', text: gettext('Not collected yet') };
    if (job.is_active) return { cls: 'badge--medium', text: gettext('Collecting') };
    if (job.status === 'failed') return { cls: 'badge--high', text: gettext('Last run failed') };
    return { cls: 'badge--resolved', text: gettext('Collected') };
  }

  function csrfToken(doc) {
    var field = doc.querySelector('#logout-form [name="csrfmiddlewaretoken"]');
    return field ? field.value : '';
  }

  function Card(doc, element, collectUrl) {
    this.doc = doc;
    this.el = element;
    this.collectUrl = collectUrl;
    this.slug = element.getAttribute('data-dataset');
    this.button = element.querySelector('[data-collect]');
    this.label = element.querySelector('[data-collect-label]');
    this.status = element.querySelector('[data-status]');
    this.next = element.querySelector('[data-next]');
    this.badge = element.querySelector('[data-badge]');
    this.progress = element.querySelector('[data-progress]');
    this.bar = element.querySelector('[role="progressbar"]');
    this.fill = element.querySelector('[data-progress-fill]');
    this.job = null;
    this.failures = 0;
    this.timer = null;
    var raw = element.getAttribute('data-job');
    if (raw) {
      try { this.job = JSON.parse(raw); } catch (error) { this.job = null; }
    }
    this.button.addEventListener('click', this.collect.bind(this));
    this.render(this.job, {});
    if (this.job && this.job.is_active) this.poll();
  }

  Card.prototype.render = function (job, opts) {
    var view = buttonState(job, opts);
    this.button.setAttribute('data-state', view.state);
    this.button.disabled = view.disabled;
    this.button.setAttribute('aria-busy', view.busy ? 'true' : 'false');
    this.label.textContent = view.label;
    this.el.classList.toggle('is-busy', view.busy);

    if (opts.message !== undefined) this.setStatus(opts.message, view.state === 'error');
    else if (job) this.setStatus(job.message, job.status === 'failed');
    if (opts.next !== undefined) this.next.textContent = opts.next;
    else if (job) this.next.textContent = job.next_action;

    if (!job) return;
    var badge = badgeFor(job);
    var span = this.doc.createElement('span');
    span.className = 'badge ' + badge.cls;
    span.textContent = badge.text;
    this.badge.replaceChildren(span);

    this.progress.hidden = !job.is_active;
    if (job.progress === null || job.progress === undefined) {
      this.bar.removeAttribute('aria-valuenow');
      this.bar.classList.add('bar--indeterminate');
    } else {
      this.bar.setAttribute('aria-valuenow', String(job.progress));
      this.bar.classList.remove('bar--indeterminate');
      this.fill.style.width = job.progress + '%';
    }
    if (job.status === 'succeeded') {
      this.count('records', job.dataset_record_count);
      this.count('labels', job.dataset_label_count);
      this.count('new', job.records_new);
      var when = this.el.querySelector('[data-last-collected]');
      if (when) when.textContent = job.last_collected;
    }
  };

  Card.prototype.count = function (name, value) {
    var cell = this.el.querySelector('[data-count="' + name + '"]');
    if (cell) cell.textContent = String(value);
  };

  Card.prototype.setStatus = function (text, isError) {
    this.status.textContent = text || '';
    this.status.classList.toggle('is-error', !!isError);
  };

  Card.prototype.collect = function () {
    if (this.button.disabled) return;
    var card = this;
    card.render(card.job, { requesting: true, message: gettext('Starting collection…'), next: '' });
    fetch(card.collectUrl, {
      method: 'POST',
      credentials: 'same-origin',
      headers: { 'Content-Type': 'application/json', 'X-CSRFToken': csrfToken(card.doc), 'Accept': 'application/json' },
      body: JSON.stringify({ dataset: card.slug })
    }).then(function (response) {
      return response.json().catch(function () { return {}; }).then(function (body) {
        return { status: response.status, body: body };
      });
    }).then(function (result) {
      if ((result.status === 202 || result.status === 409) && result.body.job) {
        card.update(result.body.job);
        if (result.status === 409) card.setStatus(result.body.error + ' ' + result.body.job.message, false);
        return;
      }
      if (result.status === 401) {
        card.render(card.job, { disabled: true, message: result.body.error || gettext('Please sign in again.'),
                                next: gettext('Your session has ended. Reload the page and sign in.') });
        card.setStatus(card.status.textContent, true);
        return;
      }
      card.render(card.job, { requestError: true,
                              message: result.body.error || gettext('Collection could not be started. Please try again.'),
                              next: '' });
    }).catch(function () {
      card.render(card.job, { requestError: true,
                              message: gettext('Could not reach the server. Check your connection and try again.'),
                              next: '' });
    });
  };

  Card.prototype.update = function (job) {
    this.job = job;
    this.failures = 0;
    this.render(job, {});
    updateTotals(this.doc);
    if (job.is_active) this.poll();
  };

  Card.prototype.poll = function () {
    var card = this;
    clearTimeout(card.timer);
    card.timer = setTimeout(function () {
      fetch(card.job.status_url, { credentials: 'same-origin', headers: { 'Accept': 'application/json' } })
        .then(function (response) {
          if (!response.ok) throw new Error(String(response.status));
          return response.json();
        })
        .then(function (body) { card.update(body.job); })
        .catch(function () {
          card.failures += 1;
          if (card.failures < MAX_POLL_FAILURES) { card.poll(); return; }
          card.render(card.job, { requestError: true,
                                  message: gettext('Lost contact with the server. The job may still be running; reload the page to check.'),
                                  next: '' });
        });
    }, POLL_MS);
  };

  function updateTotals(doc) {
    var records = 0;
    var running = 0;
    doc.querySelectorAll('[data-dataset-card]').forEach(function (element) {
      var cell = element.querySelector('[data-count="records"]');
      records += parseInt(cell && cell.textContent, 10) || 0;
      if (element.classList.contains('is-busy')) running += 1;
    });
    var total = doc.querySelector('[data-total-records]');
    var busy = doc.querySelector('[data-total-running]');
    if (total) total.textContent = records.toLocaleString();
    if (busy) busy.textContent = String(running);
  }

  function init(doc) {
    var urlHolder = doc.querySelector('[data-collect-url]');
    if (!urlHolder) return;
    var collectUrl = urlHolder.getAttribute('data-collect-url');
    doc.querySelectorAll('[data-dataset-card]').forEach(function (element) {
      new Card(doc, element, collectUrl);
    });
  }

  return { buttonState: buttonState, badgeFor: badgeFor, init: init };
}));
