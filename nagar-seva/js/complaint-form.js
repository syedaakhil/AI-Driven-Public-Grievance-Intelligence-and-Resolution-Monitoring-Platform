/* ==========================================================================
   City Corporation — citizen complaint registration

   A valid form is sent as multipart FormData (with the photo and previous copy)
   to POST /api/complaints/ (grievances.views.api_complaints), which validates it
   again with CitizenComplaintForm and answers with the CC-YYYY-NNNNN reference.
   The page must be opened through Django (/citizen/complaint/), not Live Server.
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

  // Form field id -> CitizenComplaintForm field name (used in the request and in the server's errors).
  var SERVER_FIELDS = {
    fullName: 'citizen_name',
    address: 'citizen_address',
    taluk: 'taluk',
    description: 'description',
    photo: 'attachment',
    affectedArea: 'location',
    impact: 'impact',
    previouslyReported: 'reported_before',
    previousReference: 'previous_reference',
    previousCopy: 'previous_copy',
    declaration: 'declaration'
  };

  var FILE_RULES = {
    photo: {
      extensions: ['jpg', 'jpeg', 'png', 'webp'],
      types: ['image/jpeg', 'image/png', 'image/webp'],
      label: gettext('JPG, JPEG, PNG or WEBP')
    },
    previousCopy: {
      extensions: ['pdf', 'jpg', 'jpeg', 'png'],
      types: ['application/pdf', 'image/jpeg', 'image/png'],
      label: gettext('PDF, JPG, JPEG or PNG')
    }
  };

  // Fields in on-screen order, so the error summary lists them top to bottom.
  var FIELD_ORDER = [
    'fullName', 'address', 'taluk', 'description', 'photo', 'affectedArea', 'impact',
    'previouslyReported', 'previousReference', 'previousCopy', 'declaration'
  ];
  var PREVIOUS_FIELDS = ['previousReference', 'previousCopy'];
  var NAME_PATTERN = /^[\p{L}\p{M}][\p{L}\p{M} .'-]*$/u;
  var HAS_LETTER = /\p{L}/u;
  var REFERENCE_PATTERN = /^[A-Za-z0-9][A-Za-z0-9 \/.-]*$/;

  var form = document.getElementById('complaintForm');
  var submitBtn = document.getElementById('submitBtn');
  var clearBtn = document.getElementById('clearBtn');
  var errorSummary = document.getElementById('errorSummary');
  var errorSummaryTitle = document.getElementById('errorSummaryTitle');
  var errorSummaryList = document.getElementById('errorSummaryList');
  var previousDetails = document.getElementById('previousDetails');
  var clearDialog = document.getElementById('clearDialog');
  var successPanel = document.getElementById('successPanel');

  var state = {
    files: { photo: null, previousCopy: null },
    previewUrls: { photo: null, previousCopy: null },
    attempted: false,   // true after the first Submit click: errors then update live
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
    return selectedRadio('previouslyReported') === 'yes';
  }

  function isFieldActive(name) {
    return PREVIOUS_FIELDS.indexOf(name) === -1 || isPreviousVisible();
  }

  /* Validation ----------------------------------------------------------- */

  function requiredText(name, emptyMessage, minLength, shortMessage) {
    var text = value(name);
    if (!text) return emptyMessage;
    if (text.length < minLength) return shortMessage;
    return '';
  }

  var validators = {
    fullName: function () {
      var message = requiredText('fullName', gettext('Enter your full name.'), 2, gettext('Your name must have at least 2 characters.'));
      if (message) return message;
      if (!NAME_PATTERN.test(value('fullName'))) return gettext('Use letters, spaces, full stops, apostrophes or hyphens in your name.');
      return '';
    },
    address: function () {
      return requiredText('address', gettext('Enter your residential address.'), 10, gettext('Please enter your full address, including house number and street.'));
    },
    taluk: function () {
      var message = requiredText('taluk', gettext('Enter your taluk.'), 2, gettext('The taluk name must have at least 2 characters.'));
      if (message) return message;
      if (!HAS_LETTER.test(value('taluk'))) return gettext('Enter a valid taluk name.');
      return '';
    },
    description: function () {
      return requiredText('description', gettext('Describe the problem you are facing.'), 20, gettext('Please describe the problem in a little more detail (at least 20 characters).'));
    },
    photo: function () {
      return state.files.photo ? checkFile('photo', state.files.photo) : '';   // optional
    },
    affectedArea: function () {
      return requiredText('affectedArea', gettext('Enter the area affected by the problem.'), 3, gettext('Please enter the locality, street, village, ward or landmark.'));
    },
    impact: function () {
      return requiredText('impact', gettext('Explain how this problem affects you.'), 10, gettext('Please explain the effect in a little more detail (at least 10 characters).'));
    },
    previouslyReported: function () {
      return selectedRadio('previouslyReported') ? '' : gettext('Select Yes or No.');
    },
    previousReference: function () {
      var text = value('previousReference');
      if (text && !REFERENCE_PATTERN.test(text)) return gettext('Use only letters, numbers, spaces, hyphens, full stops or slashes.');
      return '';
    },
    previousCopy: function () {
      return state.files.previousCopy ? checkFile('previousCopy', state.files.previousCopy) : '';   // optional
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

    if (name === 'previouslyReported') {
      form.querySelector('[data-field="previouslyReported"]').setAttribute('aria-invalid', invalid);
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

  function focusTarget(name) {
    if (name === 'previouslyReported') return form.querySelector('input[name="previouslyReported"]');
    return document.getElementById(name);
  }

  function renderSummary(errors) {
    errorSummaryList.textContent = '';
    if (!errors.length) {
      errorSummary.hidden = true;
      return;
    }
    errorSummaryTitle.textContent = interpolate(ngettext('Please correct %(count)s problem before submitting',
                          'Please correct %(count)s problems before submitting', errors.length),
                 { count: errors.length }, true);
    errors.forEach(function (error) {
      var item = document.createElement('li');
      if (!error.name) {   // a server message that belongs to no single field
        item.textContent = error.message;
        errorSummaryList.appendChild(item);
        return;
      }
      var link = document.createElement('a');
      link.href = '#' + (focusTarget(error.name) || {}).id;
      link.textContent = error.message;
      link.addEventListener('click', function (event) {
        event.preventDefault();
        var target = focusTarget(error.name);
        target.closest('.field').scrollIntoView({ block: 'center' });
        target.focus({ preventScroll: true });
      });
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
    if (name === 'previouslyReported') {
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
    // Hidden fields keep what was typed (in case the citizen switches back) but are not validated or saved.
    if (!show) PREVIOUS_FIELDS.forEach(function (name) { setError(name, ''); });
  }

  /* File uploads --------------------------------------------------------- */

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
    document.getElementById(name).value = '';
    renderUpload(name);
  }

  Object.keys(FILE_RULES).forEach(function (name) {
    var input = document.getElementById(name);
    var upload = form.querySelector('[data-upload="' + name + '"]');
    var thumb = upload.querySelector('.upload__thumb');

    input.addEventListener('change', function () {
      var file = input.files && input.files[0];
      if (!file) return;   // picker cancelled: keep the current selection
      var message = checkFile(name, file);
      input.value = '';    // the chosen File is kept in state; the input itself is not submitted
      if (message) {
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
    var hasText = ['fullName', 'address', 'taluk', 'description', 'affectedArea', 'impact', 'previousReference']
      .some(function (name) { return value(name) !== ''; });
    return hasText ||
      Boolean(selectedRadio('previouslyReported')) ||
      form.elements.declaration.checked ||
      Boolean(state.files.photo || state.files.previousCopy);
  }

  function resetForm() {
    form.reset();
    Object.keys(FILE_RULES).forEach(removeFile);
    FIELD_ORDER.forEach(function (name) { setError(name, ''); });
    renderSummary([]);
    state.attempted = false;
    togglePreviousDetails();
    updateCounter('description');
    updateCounter('impact');
  }

  function clearAndFocus() {
    resetForm();
    document.getElementById('fullName').focus();
  }

  clearBtn.addEventListener('click', function () {
    if (!isDirty()) {
      clearAndFocus();
      return;
    }
    if (typeof clearDialog.showModal === 'function') {
      clearDialog.showModal();
    } else if (window.confirm(gettext('Clear the form? Everything you have entered will be removed.'))) {
      clearAndFocus();
    }
  });

  clearDialog.addEventListener('click', function (event) {
    var button = event.target.closest('[data-dialog]');
    if (!button) return;
    clearDialog.close();
    if (button.dataset.dialog === 'confirm') clearAndFocus();
  });

  /* Server --------------------------------------------------------------- */

  // The selected files live in `state` (the inputs are emptied), so the body is built by hand.
  function buildFormData() {
    var data = new FormData();
    ['fullName', 'address', 'taluk', 'description', 'affectedArea', 'impact'].forEach(function (name) {
      data.append(SERVER_FIELDS[name], value(name));
    });
    data.append(SERVER_FIELDS.previouslyReported, selectedRadio('previouslyReported'));
    if (form.elements.declaration.checked) data.append(SERVER_FIELDS.declaration, 'on');
    if (state.files.photo) data.append(SERVER_FIELDS.photo, state.files.photo, state.files.photo.name);
    if (isPreviousVisible()) {
      data.append(SERVER_FIELDS.previousReference, value('previousReference'));
      if (state.files.previousCopy) {
        data.append(SERVER_FIELDS.previousCopy, state.files.previousCopy, state.files.previousCopy.name);
      }
    }
    return data;
  }

  // Puts the server's messages next to their fields and lists them in the error summary.
  function showServerErrors(serverErrors) {
    var errors = [];
    var known = {};
    FIELD_ORDER.forEach(function (name) {
      var messages = serverErrors[SERVER_FIELDS[name]];
      known[SERVER_FIELDS[name]] = true;
      var message = messages && messages.length ? messages[0] : '';
      setError(name, message);
      if (message) errors.push({ name: name, message: message });
    });
    Object.keys(serverErrors).forEach(function (key) {
      if (!known[key]) serverErrors[key].forEach(function (message) { errors.push({ name: '', message: message }); });
    });
    if (!errors.length) errors.push({ name: '', message: gettext('The complaint could not be registered. Please check your details and try again.') });
    renderSummary(errors);
    errorSummary.scrollIntoView({ block: 'start' });
    errorSummary.focus({ preventScroll: true });
  }

  function showProblem(title, text) {
    errorSummaryTitle.textContent = title;
    errorSummaryList.textContent = '';
    var item = document.createElement('li');
    item.textContent = text;
    errorSummaryList.appendChild(item);
    errorSummary.hidden = false;
    errorSummary.scrollIntoView({ block: 'start' });
    errorSummary.focus({ preventScroll: true });
  }

  function readJson(response) {
    return response.json().catch(function () { return {}; });
  }

  /* Submit --------------------------------------------------------------- */

  function setSubmitting(isSubmitting) {
    state.submitting = isSubmitting;
    submitBtn.disabled = isSubmitting;
    clearBtn.disabled = isSubmitting;
    submitBtn.textContent = isSubmitting ? gettext('Submitting…') : gettext('Submit Complaint');
    form.setAttribute('aria-busy', String(isSubmitting));
  }

  function addSummaryRow(list, label, text) {
    var dt = document.createElement('dt');
    var dd = document.createElement('dd');
    dt.textContent = label;
    dd.textContent = text;
    list.appendChild(dt);
    list.appendChild(dd);
  }

  // `complaint` is the server's reply from POST /api/complaints/.
  function showSuccess(complaint) {
    var summary = document.getElementById('successSummary');
    document.getElementById('successRef').textContent = complaint.reference;
    document.getElementById('copyStatus').textContent = '';
    summary.textContent = '';

    addSummaryRow(summary, gettext('Submitted on'), new Date(complaint.submitted_at).toLocaleString(document.documentElement.lang === 'hi' ? 'hi-IN' : 'en-IN', { dateStyle: 'medium', timeStyle: 'short' }));
    addSummaryRow(summary, gettext('Name'), complaint.citizen_name);
    addSummaryRow(summary, gettext('Taluk'), complaint.taluk);
    addSummaryRow(summary, gettext('Affected area'), complaint.location);
    addSummaryRow(summary, gettext('Problem photo'), complaint.attachment || gettext('Not attached'));
    var previous = gettext('No');
    if (complaint.previously_reported) {
      previous = gettext('Yes') + (complaint.previous_reference ? ' · ' + gettext('Ref.') + ' ' + complaint.previous_reference : ' · ' + gettext('reference not provided'));
      if (complaint.previous_copy) previous += ' · ' + interpolate(gettext('copy: %(name)s'), { name: complaint.previous_copy }, true);
    }
    addSummaryRow(summary, gettext('Reported before'), previous);
    addSummaryRow(summary, gettext('Status'), complaint.status);

    form.hidden = true;
    successPanel.hidden = false;
    window.scrollTo(0, 0);
    successPanel.focus();
  }

  form.addEventListener('submit', function (event) {
    event.preventDefault();
    if (state.submitting) return;

    state.attempted = true;
    var errors = validateAll();
    if (errors.length) {
      renderSummary(errors);
      errorSummary.scrollIntoView({ block: 'start' });
      errorSummary.focus({ preventScroll: true });
      return;
    }
    renderSummary([]);

    setSubmitting(true);
    fetch(form.action, {
      method: 'POST',
      body: buildFormData(),
      credentials: 'same-origin',
      headers: {
        'X-CSRFToken': form.elements.csrfmiddlewaretoken.value,
        'Accept': 'application/json'
      }
    }).then(function (response) {
      return readJson(response).then(function (data) {
        if (response.status === 201 && data.complaint) {
          showSuccess(data.complaint);
          resetForm();
        } else if (response.status === 401) {
          // Mobile number not verified (or the session ended): sign in again.
          window.location.href = data.login_url || form.dataset.loginUrl;
          return;
        } else if (response.status === 400 && data.errors) {
          showServerErrors(data.errors);
        } else if (response.status === 403) {
          showProblem(gettext('Your session has expired'), gettext('Please reload this page and submit the complaint again.'));
        } else {
          showProblem(gettext('Your complaint could not be registered'), gettext('Something went wrong on our side. Please try again in a few minutes.'));
        }
        setSubmitting(false);
      });
    }).catch(function () {
      setSubmitting(false);
      showProblem(gettext('Your complaint could not be sent'), gettext('Check your internet connection and submit again. Your details are still in the form.'));
    });
  });

  /* Success panel actions ------------------------------------------------ */

  document.getElementById('copyRef').addEventListener('click', function () {
    var reference = document.getElementById('successRef').textContent;
    var status = document.getElementById('copyStatus');
    var label = this.querySelector('span');
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

  document.getElementById('newComplaint').addEventListener('click', function () {
    successPanel.hidden = true;
    form.hidden = false;
    resetForm();
    window.scrollTo(0, 0);
    document.getElementById('fullName').focus();
  });

  /* Start ---------------------------------------------------------------- */

  togglePreviousDetails();
  updateCounter('description');
  updateCounter('impact');
})();
