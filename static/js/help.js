/* City Corporation — help pages: topic search on the help desk,
   and the accessibility controls on Help & support. Text comes from Django's JavaScript
   translation catalogue (gettext), so it follows the site language. */
(function () {
  'use strict';

  var ngettext = window.ngettext || function (one, many, n) { return n === 1 ? one : many; };
  var interpolate = window.interpolate || function (text, values) {
    return text.replace(/%\((\w+)\)s/g, function (_, key) { return values[key]; });
  };

  /* ---------- Help desk topic search ---------- */
  var search = document.querySelector('[data-topic-search]');
  if (search) {
    var topics = document.querySelectorAll('[data-topic]');
    var count = document.querySelector('[data-topic-count]');
    var empty = document.querySelector('[data-topic-empty]');

    var runSearch = function () {
      var phrase = search.value.trim();
      var words = phrase.toLowerCase().split(/\s+/).filter(Boolean);
      var shown = 0;
      topics.forEach(function (topic) {
        var text = (topic.getAttribute('data-topic') + ' ' + topic.textContent).toLowerCase();
        var match = words.every(function (word) { return text.indexOf(word) !== -1; });
        topic.hidden = !match;
        if (match) shown++;
        if (topic.tagName === 'DETAILS') topic.open = words.length > 0 && match;
      });
      // Hide a section heading when nothing under it matches.
      document.querySelectorAll('[data-topic-heading]').forEach(function (heading) {
        var section = heading.closest('section');
        heading.hidden = !!section && !section.querySelector('[data-topic]:not([hidden])');
      });
      empty.hidden = shown > 0;
      count.textContent = words.length
        ? interpolate(ngettext('%(count)s help topic matches “%(phrase)s”.',
                               '%(count)s help topics match “%(phrase)s”.', shown),
                      { count: shown, phrase: phrase }, true)
        : '';
    };

    search.addEventListener('input', runSearch);
    document.querySelectorAll('[data-topic-tag]').forEach(function (tag) {
      tag.addEventListener('click', function () {
        search.value = tag.getAttribute('data-topic-tag');
        runSearch();
        search.focus();
      });
    });
  }

  /* ---------- Accessibility (uses window.CityCorp from app.js) ---------- */
  var site = window.CityCorp;
  if (!site || !document.querySelector('[data-pref-text], [data-pref-toggle]')) return;

  function syncControls() {
    var prefs = site.prefs();
    document.querySelectorAll('[data-pref-text]').forEach(function (button) {
      button.setAttribute('aria-pressed', String(button.getAttribute('data-pref-text') === prefs.text));
    });
    document.querySelectorAll('[data-pref-toggle]').forEach(function (box) {
      box.checked = !!prefs[box.getAttribute('data-pref-toggle')];
    });
  }

  document.querySelectorAll('[data-pref-text]').forEach(function (button) {
    button.addEventListener('click', function () { site.setPref('text', button.getAttribute('data-pref-text')); });
  });
  document.querySelectorAll('[data-pref-toggle]').forEach(function (box) {
    box.addEventListener('change', function () { site.setPref(box.getAttribute('data-pref-toggle'), box.checked); });
  });
  document.addEventListener('citycorp:prefs', syncControls);
  syncControls();
})();
