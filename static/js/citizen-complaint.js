/* ==========================================================================
   City Corporation — citizen complaint registration

   Checks the form in the browser before it is posted to Django, previews the
   chosen files, and shows/hides the earlier-complaint fields. The server checks
   everything again (grievances/forms.py: CitizenComplaintForm).
   ========================================================================== */
(function () {
  'use strict';

  // Translations from Django's JavaScript catalogue (/jsi18n/); English if it didn't load.
  var gettext = window.gettext || function (text) { return text; };
  var ngettext = window.ngettext || function (one, many, n) { return n === 1 ? one : many; };
  var interpolate = window.interpolate || function (text, values) {
    return text.replace(/%\((\w+)\)s/g, function (_, key) { return values[key]; });
  };

  var MAX_FILE_BYTES = 5 * 1024 * 1024;

  var FILE_RULES = {
    attachment: {
      extensions: ['jpg', 'jpeg', 'png', 'webp'],
      types: ['image/jpeg', 'image/png', 'image/webp'],
      label: gettext('JPG, JPEG, PNG or WEBP')
    },
    previous_copy: {
      extensions: ['pdf', 'jpg', 'jpeg', 'png'],
      types: ['application/pdf', 'image/jpeg', 'image/png'],
      label: gettext('PDF, JPG, JPEG or PNG')
    }
  };

  // Fields in on-screen order, so the error summary lists them top to bottom.
  var FIELD_ORDER = [
    'citizen_name', 'citizen_address', 'taluk', 'description', 'attachment', 'location', 'impact',
    'reported_before', 'previous_reference', 'previous_copy', 'declaration'
  ];
  var TEXT_FIELDS = ['citizen_name', 'citizen_address', 'taluk', 'description', 'location', 'impact', 'previous_reference'];
  var PREVIOUS_FIELDS = ['previous_reference', 'previous_copy'];
  var NAME_PATTERN = /^[\p{L}\p{M}][\p{L}\p{M} .'-]*$/u;
  var HAS_LETTER = /\p{L}/u;
  var REFERENCE_PATTERN = /^[A-Za-z0-9][A-Za-z0-9 \/.-]*$/;

  initCopyButton();

  var form = document.getElementById('complaintForm');
  if (!form) {
    // Success view: move focus to the confirmation so screen readers announce it.
    var success = document.getElementById('successPanel');
    if (success) success.focus();
    return;
  }

  var submitBtn = document.getElementById('submitBtn');
  var clearBtn = document.getElementById('clearBtn');
  var errorSummary = document.getElementById('errorSummary');
  var errorSummaryTitle = document.getElementById('errorSummaryTitle');
  var errorSummaryList = document.getElementById('errorSummaryList');
  var previousDetails = document.getElementById('previousDetails');
  var clearDialog = document.getElementById('clearDialog');

  var state = {
    files: { attachment: null, previous_copy: null },
    previewUrls: { attachment: null, previous_copy: null },
    attempted: !errorSummary.hidden,   // the server sent errors back: keep them updating live
    submitting: false
  };

  /* Helpers -------------------------------------------------------------- */

  function value(name) {
    var el = form.elements[name];
    return el && typeof el.value === 'string' ? el.value.trim() : '';
  }

  function selectedRadio(name) {
    var checked = form.querySelector('input[name="' + name + '"]:checked');
    return checked ? checked.value : '';
  }

  function formatSize(bytes) {
    if (bytes < 1024) return interpolate(gettext('%(count)s bytes'), { count: bytes }, true);
    if (bytes < 1024 * 1024) return (bytes / 1024).toFixed(0) + ' KB';
    return (bytes / (1024 * 1024)).toFixed(2) + ' MB';
  }

  function fileExtension(fileName) {
    var dot = fileName.lastIndexOf('.');
    return dot === -1 ? '' : fileName.slice(dot + 1).toLowerCase();
  }

  function isPreviousVisible() {
    return selectedRadio('reported_before') === 'yes';
  }

  function isFieldActive(name) {
    return PREVIOUS_FIELDS.indexOf(name) === -1 || isPreviousVisible();
  }

  /* Validation (same rules as the server) -------------------------------- */

  function requiredText(name, emptyMessage, minLength, shortMessage) {
    var text = value(name);
    if (!text) return emptyMessage;
    if (text.length < minLength) return shortMessage;
    return '';
  }

  var validators = {
    citizen_name: function () {
      var message = requiredText('citizen_name', gettext('Enter your full name.'), 2, gettext('Your name must have at least 2 characters.'));
      if (message) return message;
      if (!NAME_PATTERN.test(value('citizen_name'))) return gettext('Use letters, spaces, full stops, apostrophes or hyphens in your name.');
      return '';
    },
    citizen_address: function () {
      return requiredText('citizen_address', gettext('Enter your residential address.'), 10, gettext('Please enter your full address, including house number and street.'));
    },
    taluk: function () {
      var message = requiredText('taluk', gettext('Enter your taluk.'), 2, gettext('Enter a valid taluk name.'));
      if (message) return message;
      if (!HAS_LETTER.test(value('taluk'))) return gettext('Enter a valid taluk name.');
      return '';
    },
    description: function () {
      return requiredText('description', gettext('Describe the problem you are facing.'), 20, gettext('Please describe the problem in a little more detail (at least 20 characters).'));
    },
    attachment: function () {
      return state.files.attachment ? checkFile('attachment', state.files.attachment) : '';   // optional
    },
    location: function () {
      return requiredText('location', gettext('Enter the area affected by the problem.'), 3, gettext('Please enter the locality, street, village, ward or landmark.'));
    },
    impact: function () {
      return requiredText('impact', gettext('Explain how this problem affects you.'), 10, gettext('Please explain the effect in a little more detail (at least 10 characters).'));
    },
    reported_before: function () {
      return selectedRadio('reported_before') ? '' : gettext('Select Yes or No.');
    },
    previous_reference: function () {
      var text = value('previous_reference');
      if (text && !REFERENCE_PATTERN.test(text)) return gettext('Use only letters, numbers, spaces, hyphens, full stops or slashes.');
      return '';
    },
    previous_copy: function () {
      return state.files.previous_copy ? checkFile('previous_copy', state.files.previous_copy) : '';   // optional
    },
    declaration: function () {
      return form.elements.declaration.checked ? '' : gettext('Please confirm that the information you have provided is accurate.');
    }
  };

  function checkFile(name, file) {
    var rule = FILE_RULES[name];
    var extOk = rule.extensions.indexOf(fileExtension(file.name)) !== -1;
    var typeOk = !file.type || rule.types.indexOf(file.type) !== -1;
    if (!extOk || !typeOk) return interpolate(gettext('“%(name)s” is not an accepted format. Please choose a %(formats)s file.'), { name: file.name, formats: rule.label }, true);
    if (file.size === 0) return interpolate(gettext('“%(name)s” is empty. Please choose another file.'), { name: file.name }, true);
    if (file.size > MAX_FILE_BYTES) return interpolate(gettext('“%(name)s” (%(size)s) is larger than the 5 MB limit. Please choose a smaller file.'), { name: file.name, size: formatSize(file.size) }, true);
    return '';
  }

  // Marks a field as valid or invalid and writes its inline message.
  function setError(name, message) {
    var errorEl = document.getElementById(name + '-error');
    if (errorEl) errorEl.textContent = message;
    var invalid = message ? 'true' : 'false';

    if (name === 'reported_before') {
      form.querySelector('[data-field="reported_before"]').setAttribute('aria-invalid', invalid);
    } else if (name === 'declaration') {
      form.elements.declaration.setAttribute('aria-invalid', invalid);
      form.elements.declaration.closest('.checkbox').setAttribute('data-invalid', invalid);
    } else if (FILE_RULES[name]) {
      document.getElementById(name).setAttribute('aria-invalid', invalid);
      form.querySelector('[data-upload="' + name + '"]').setAttribute('data-invalid', invalid);
    } else {
      document.getElementById(name).setAttribute('aria-invalid', invalid);
    }
  }

  function hasError(name) {
    var errorEl = document.getElementById(name + '-error');
    return Boolean(errorEl && errorEl.textContent);
  }

  function validateField(name) {
    if (!isFieldActive(name)) {
      setError(name, '');
      return '';
    }
    var message = validators[name]();
    // A rejected file is never attached, so its message stays visible but does not block submitting.
    if (FILE_RULES[name] && !message) return '';
    setError(name, message);
    return message;
  }

  function validateAll() {
    var errors = [];
    FIELD_ORDER.forEach(function (name) {
      var message = validateField(name);
      if (message) errors.push({ name: name, message: message });
    });
    return errors;
  }

  function focusField(id) {
    var target = document.getElementById(id);
    if (!target) return;
    var field = target.closest('.field');
    (field || target).scrollIntoView({ block: 'center' });
    target.focus({ preventScroll: true });
  }

  // Links in the summary (from the server or from this script) jump to their field.
  errorSummaryList.addEventListener('click', function (event) {
    var link = event.target.closest('a[href^="#"]');
    if (!link) return;
    event.preventDefault();
    focusField(link.getAttribute('href').slice(1));
  });

  function renderSummary(errors) {
    errorSummaryList.textContent = '';
    var note = errorSummary.querySelector('.alert__note');
    if (note) note.remove();
    if (!errors.length) {
      errorSummary.hidden = true;
      return;
    }
    errorSummaryTitle.textContent = interpolate(ngettext('Please correct %(count)s problem before submitting',
                          'Please correct %(count)s problems before submitting', errors.length),
                 { count: errors.length }, true);
    errors.forEach(function (error) {
      var item = document.createElement('li');
      var link = document.createElement('a');
      link.href = '#' + error.name;
      link.textContent = error.message;
      item.appendChild(link);
      errorSummaryList.appendChild(item);
    });
    errorSummary.hidden = false;
  }

  function refreshSummary() {
    if (!state.attempted || errorSummary.hidden) return;
    var errors = FIELD_ORDER
      .filter(function (name) { return isFieldActive(name) && !FILE_RULES[name] && hasError(name); })
      .map(function (name) { return { name: name, message: document.getElementById(name + '-error').textContent }; });
    renderSummary(errors);
  }

  /* Live feedback -------------------------------------------------------- */

  function updateCounter(name) {
    var counter = document.getElementById(name + '-count');
    var el = form.elements[name];
    if (counter && el) counter.textContent = el.value.length + ' / ' + el.maxLength;
  }

  form.addEventListener('input', function (event) {
    var name = event.target.name;
    if (!validators[name] || FILE_RULES[name]) return;
    updateCounter(name);
    if (state.attempted || hasError(name)) {
      validateField(name);
      refreshSummary();
    }
  });

  form.addEventListener('focusout', function (event) {
    var name = event.target.name;
    if (!validators[name] || FILE_RULES[name] || event.target.type === 'radio' || event.target.type === 'checkbox') return;
    if (state.attempted || value(name)) {
      validateField(name);
      refreshSummary();
    }
  });

  form.addEventListener('change', function (event) {
    var name = event.target.name;
    if (name === 'reported_before') {
      togglePreviousDetails();
      validateField(name);
      refreshSummary();
    } else if (name === 'declaration' && (state.attempted || hasError(name))) {
      validateField(name);
      refreshSummary();
    }
  });

  function togglePreviousDetails() {
    var show = isPreviousVisible();
    previousDetails.hidden = !show;
    // Hidden fields keep what was typed (in case the citizen switches back); the server ignores them on "No".
    if (!show) PREVIOUS_FIELDS.forEach(function (name) { setError(name, ''); });
  }

  /* File uploads --------------------------------------------------------- */

  // Puts a File back into an <input type="file"> (or empties it).
  function setInputFile(input, file) {
    if (!file) {
      input.value = '';
      return;
    }
    try {
      var transfer = new DataTransfer();
      transfer.items.add(file);
      input.files = transfer.files;
    } catch (err) {
      // Very old browsers: the selection simply stays as the browser left it.
    }
  }

  function renderUpload(name) {
    var upload = form.querySelector('[data-upload="' + name + '"]');
    var input = document.getElementById(name);
    var drop = upload.querySelector('.upload__drop');
    var preview = upload.querySelector('.upload__preview');
    var thumb = upload.querySelector('.upload__thumb');
    var fileIcon = upload.querySelector('.upload__file-icon');
    var file = state.files[name];

    if (state.previewUrls[name]) {
      URL.revokeObjectURL(state.previewUrls[name]);
      state.previewUrls[name] = null;
    }

    if (!file) {
      drop.hidden = false;
      preview.hidden = true;
      thumb.removeAttribute('src');
      input.removeAttribute('tabindex');
      return;
    }

    var isImage = file.type.indexOf('image/') === 0;
    if (isImage) {
      state.previewUrls[name] = URL.createObjectURL(file);
      thumb.src = state.previewUrls[name];
    } else {
      thumb.removeAttribute('src');
    }
    thumb.hidden = !isImage;
    if (fileIcon) fileIcon.hidden = isImage;

    upload.querySelector('.upload__name').textContent = file.name;
    upload.querySelector('.upload__size').textContent =
      (fileExtension(file.name).toUpperCase() || gettext('File')) + ' · ' + formatSize(file.size);
    drop.hidden = true;
    preview.hidden = false;
    // The Replace and Remove buttons take over keyboard access while a file is selected.
    input.setAttribute('tabindex', '-1');
  }

  function removeFile(name) {
    state.files[name] = null;
    setInputFile(document.getElementById(name), null);
    renderUpload(name);
  }

  Object.keys(FILE_RULES).forEach(function (name) {
    var input = document.getElementById(name);
    var upload = form.querySelector('[data-upload="' + name + '"]');
    var thumb = upload.querySelector('.upload__thumb');

    input.addEventListener('change', function () {
      var file = input.files && input.files[0];
      if (!file) {
        // Picker cancelled (some browsers empty the input): keep the current selection.
        setInputFile(input, state.files[name]);
        return;
      }
      var message = checkFile(name, file);
      if (message) {
        setInputFile(input, state.files[name]);   // never post a rejected file
        setError(name, state.files[name]
          ? message + ' ' + interpolate(gettext('Your earlier file “%(name)s” is still selected.'), { name: state.files[name].name }, true)
          : message + ' ' + gettext('No file has been attached.'));
        refreshSummary();
        return;
      }
      state.files[name] = file;
      setError(name, '');
      renderUpload(name);
      refreshSummary();
    });

    // A file with an image extension that cannot actually be displayed is rejected.
    thumb.addEventListener('error', function () {
      var file = state.files[name];
      if (!file || !thumb.getAttribute('src')) return;
      removeFile(name);
      setError(name, interpolate(gettext('“%(name)s” could not be opened as an image. Please choose another file.'), { name: file.name }, true));
      refreshSummary();
    });

    upload.addEventListener('click', function (event) {
      var button = event.target.closest('[data-action]');
      if (!button) return;
      if (button.dataset.action === 'replace') {
        input.click();
      } else if (button.dataset.action === 'remove') {
        removeFile(name);
        setError(name, '');
        refreshSummary();
        input.focus();
      }
    });
  });

  /* Clear form ----------------------------------------------------------- */

  function isDirty() {
    var hasText = TEXT_FIELDS.some(function (name) { return value(name) !== ''; });
    return hasText ||
      Boolean(selectedRadio('reported_before')) ||
      form.elements.declaration.checked ||
      Boolean(state.files.attachment || state.files.previous_copy);
  }

  // form.reset() would restore values the server sent back, so every field is emptied by hand.
  function clearFields() {
    TEXT_FIELDS.forEach(function (name) { form.elements[name].value = ''; });
    form.querySelectorAll('input[name="reported_before"]').forEach(function (radio) { radio.checked = false; });
    form.elements.declaration.checked = false;
    Object.keys(FILE_RULES).forEach(removeFile);
    FIELD_ORDER.forEach(function (name) { setError(name, ''); });
    renderSummary([]);
    state.attempted = false;
    togglePreviousDetails();
    updateCounter('description');
    updateCounter('impact');
    document.getElementById('citizen_name').focus();
  }

  clearBtn.addEventListener('click', function () {
    if (!isDirty()) {
      clearFields();
      return;
    }
    if (typeof clearDialog.showModal === 'function') {
      clearDialog.showModal();
    } else if (window.confirm(gettext('Clear the form? Everything you have entered will be removed.'))) {
      clearFields();
    }
  });

  clearDialog.addEventListener('click', function (event) {
    var button = event.target.closest('[data-dialog]');
    if (!button) return;
    clearDialog.close();
    if (button.dataset.dialog === 'confirm') clearFields();
  });

  /* Submit --------------------------------------------------------------- */

  function setSubmitting(isSubmitting) {
    state.submitting = isSubmitting;
    submitBtn.disabled = isSubmitting;
    clearBtn.disabled = isSubmitting;
    submitBtn.textContent = isSubmitting ? gettext('Submitting…') : gettext('Submit Complaint');
    form.setAttribute('aria-busy', String(isSubmitting));
  }

  form.addEventListener('submit', function (event) {
    if (state.submitting) {
      event.preventDefault();
      return;
    }
    state.attempted = true;
    var errors = validateAll();
    if (errors.length) {
      event.preventDefault();
      renderSummary(errors);
      errorSummary.scrollIntoView({ block: 'start' });
      errorSummary.focus({ preventScroll: true });
      return;
    }
    // Valid: let the browser post the form (with its files) and block a second click meanwhile.
    setSubmitting(true);
  });

  // Coming back with the browser's Back button can restore a disabled button.
  window.addEventListener('pageshow', function () {
    setSubmitting(false);
  });

  /* Start ---------------------------------------------------------------- */

  togglePreviousDetails();
  updateCounter('description');
  updateCounter('impact');
  if (!errorSummary.hidden) {
    errorSummary.scrollIntoView({ block: 'start' });
    errorSummary.focus({ preventScroll: true });
  }

  /* Copy reference (success view) ---------------------------------------- */

  function initCopyButton() {
    var button = document.getElementById('copyRef');
    if (!button) return;
    button.addEventListener('click', function () {
      var reference = document.getElementById('successRef').textContent.trim();
      var status = document.getElementById('copyStatus');
      var label = button.querySelector('span');
      function done(message, buttonText) {
        status.textContent = message;
        label.textContent = buttonText;
        setTimeout(function () { label.textContent = gettext('Copy'); }, 2000);
      }
      if (navigator.clipboard && navigator.clipboard.writeText) {
        navigator.clipboard.writeText(reference).then(
          function () { done(gettext('Reference number copied.'), gettext('Copied')); },
          function () { done(gettext('Could not copy. Please write the reference number down.'), gettext('Copy failed')); }
        );
      } else {
        done(gettext('Copy is not available in this browser. Please write the reference number down.'), gettext('Copy failed'));
      }
    });
  }
})();
