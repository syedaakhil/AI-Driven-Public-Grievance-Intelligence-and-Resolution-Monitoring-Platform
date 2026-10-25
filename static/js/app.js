/* City Corporation — behaviour shared by every page:
   viewer preferences, mobile menu, dropdown menus,
   dialogs, auto-submitting selects and character counters. */
(function () {
  'use strict';

  /* ---------- Viewer preferences (saved in this browser only) ---------- */
  var PREFS_KEY = 'citycorp.prefs';
  // The site language (English / Hindi) is chosen with the language menu and handled by Django
  // (set_language + translation files), not here.
  var DEFAULTS = { text: 'normal', contrast: false, reduceMotion: false };

  function readPrefs() {
    try {
      return Object.assign({}, DEFAULTS, JSON.parse(localStorage.getItem(PREFS_KEY) || '{}'));
    } catch (e) {
      return Object.assign({}, DEFAULTS);
    }
  }

  function applyPrefs(prefs) {
    var root = document.documentElement;
    root.setAttribute('data-text', prefs.text);
    root.classList.toggle('contrast', !!prefs.contrast);
    root.classList.toggle('reduce-motion', !!prefs.reduceMotion);
  }

  window.CityCorp = {
    prefs: readPrefs,
    setPref: function (key, value) {
      var prefs = readPrefs();
      prefs[key] = value;
      try { localStorage.setItem(PREFS_KEY, JSON.stringify(prefs)); } catch (e) { /* storage blocked */ }
      applyPrefs(prefs);
      document.dispatchEvent(new CustomEvent('citycorp:prefs', { detail: prefs }));
    }
  };
  applyPrefs(readPrefs());

  /* ---------- Mobile sidebar ---------- */
  var openButton = document.querySelector('[data-nav-open]');
  var backdrop = document.querySelector('.backdrop');

  function setNav(open) {
    document.body.classList.toggle('nav-open', open);
    if (backdrop) backdrop.hidden = !open;
    if (openButton) openButton.setAttribute('aria-expanded', String(open));
    if (open) {
      var firstLink = document.querySelector('#sidebar a');
      if (firstLink) firstLink.focus();
    } else if (openButton && document.activeElement && document.activeElement.closest('#sidebar')) {
      openButton.focus();
    }
  }

  if (openButton) openButton.addEventListener('click', function () { setNav(true); });
  document.querySelectorAll('[data-nav-close]').forEach(function (el) {
    el.addEventListener('click', function () { setNav(false); });
  });
  window.addEventListener('resize', function () {
    if (window.innerWidth > 1024 && document.body.classList.contains('nav-open')) setNav(false);
  });

  /* ---------- Dropdown menus (<details class="menu">) ---------- */
  var menus = Array.prototype.slice.call(document.querySelectorAll('details.menu'));
  menus.forEach(function (menu) {
    menu.addEventListener('toggle', function () {
      if (!menu.open) return;
      menus.forEach(function (other) { if (other !== menu) other.open = false; });
    });
  });
  document.addEventListener('click', function (event) {
    menus.forEach(function (menu) {
      if (menu.open && !menu.contains(event.target)) menu.open = false;
    });
  });
  document.addEventListener('keydown', function (event) {
    if (event.key !== 'Escape') return;
    var open = menus.filter(function (menu) { return menu.open; });
    if (open.length) {
      open.forEach(function (menu) { menu.open = false; });
      open[0].querySelector('summary').focus();
    } else if (document.body.classList.contains('nav-open')) {
      setNav(false);
    }
  });

  /* ---------- Selects that submit their form when changed ---------- */
  document.querySelectorAll('[data-autosubmit]').forEach(function (select) {
    select.addEventListener('change', function () { if (select.form) select.form.submit(); });
  });

  /* ---------- Dialogs ---------- */
  document.addEventListener('click', function (event) {
    var opener = event.target.closest('[data-dialog-open]');
    if (opener) {
      var dialog = document.getElementById(opener.getAttribute('data-dialog-open'));
      if (!dialog) return;
      var current = opener.closest('dialog');
      if (current) current.close();
      if (typeof dialog.showModal === 'function') dialog.showModal(); else dialog.setAttribute('open', '');
      return;
    }
    var closer = event.target.closest('[data-dialog-close]');
    if (closer) {
      var parent = closer.closest('dialog');
      if (parent) parent.close();
      return;
    }
    if (event.target.tagName === 'DIALOG') event.target.close(); // click on the dimmed backdrop
  });

  /* ---------- Character counters: <span data-counter-for="field-id"> ---------- */
  document.querySelectorAll('[data-counter-for]').forEach(function (counter) {
    var field = document.getElementById(counter.getAttribute('data-counter-for'));
    if (!field) return;
    var max = field.getAttribute('maxlength');
    function update() { counter.textContent = field.value.length + (max ? ' / ' + max : ''); }
    field.addEventListener('input', update);
    update();
  });

  // Print buttons (the employee ID card page)
  document.querySelectorAll('[data-print]').forEach(function (button) {
    button.addEventListener('click', function () { window.print(); });
  });
})();
