/* City Corporation — complaint form helpers: department hint, priority suggestion
   and photo preview. The server validates everything again on submit. */
(function () {
  'use strict';

  var form = document.getElementById('complaint-form');
  if (!form) return;

  /* ---------- Department that will handle the chosen category ---------- */
  var departments = JSON.parse(document.getElementById('departments').textContent);
  var category = form.querySelector('[name="category"]');
  var departmentLabel = form.querySelector('[data-department]');

  function showDepartment() {
    departmentLabel.textContent = departments[category.value] || '—';
  }

  /* ---------- Priority suggestion (simple keyword rule) ---------- */
  var HIGH_WORDS = ['overflow', 'sewage', 'manhole', 'accident', 'injur', 'electric', 'wire', 'shock', 'fire',
    'flood', 'collapse', 'contaminat', 'burst', 'danger', 'no water', 'hospital', 'school', 'children'];
  var MEDIUM_WORDS = ['pothole', 'leak', 'garbage', 'not working', 'broken', 'blocked', 'smell',
    'low pressure', 'debris', 'waterlog', 'days'];
  var LABELS = { high: 'High', medium: 'Medium', low: 'Low' };

  var title = form.querySelector('[name="title"]');
  var description = form.querySelector('[name="description"]');
  var suggestText = form.querySelector('[data-suggest-text]');
  var useSuggestion = form.querySelector('[data-suggest-use]');
  var suggested = null;

  function firstMatch(text, words) {
    for (var i = 0; i < words.length; i++) {
      if (text.indexOf(words[i]) !== -1) return words[i];
    }
    return null;
  }

  function suggest() {
    var text = (title.value + ' ' + description.value).toLowerCase();
    if (!category.value || text.trim().length < 12) {
      suggested = null;
      suggestText.textContent = 'Fill in the category and description to get a priority suggestion.';
      useSuggestion.hidden = true;
      return;
    }
    var reason;
    var high = firstMatch(text, HIGH_WORDS);
    if (high) {
      suggested = 'high';
      reason = 'mentions “' + high + '”';
    } else {
      var medium = firstMatch(text, MEDIUM_WORDS);
      var essential = /water|sewage/i.test(category.options[category.selectedIndex].text);
      if (medium || essential) {
        suggested = 'medium';
        reason = medium ? 'mentions “' + medium + '”' : 'essential service';
      } else {
        suggested = 'low';
        reason = 'no urgent words found';
      }
    }
    var strong = document.createElement('strong');
    strong.textContent = LABELS[suggested];
    suggestText.textContent = '';
    suggestText.append('Suggested priority: ', strong, ' (' + reason + ')');
    useSuggestion.hidden = false;
  }

  useSuggestion.addEventListener('click', function () {
    var radio = form.querySelector('input[name="priority"][value="' + suggested + '"]');
    if (radio) {
      radio.checked = true;
      radio.focus();
    }
  });

  category.addEventListener('change', function () { showDepartment(); suggest(); });
  title.addEventListener('input', suggest);
  description.addEventListener('input', suggest);
  showDepartment();
  suggest();

  /* ---------- Photo preview ---------- */
  var input = form.querySelector('input[type="file"]');
  var dropzone = input.closest('.dropzone');
  var preview = form.querySelector('[data-preview]');
  var previewImage = form.querySelector('[data-preview-img]');
  var previewName = form.querySelector('[data-preview-name]');
  var previewMeta = form.querySelector('[data-preview-meta]');
  var photoError = form.querySelector('[data-photo-error]');
  var TYPES = ['image/jpeg', 'image/png', 'image/webp'];
  var MAX_BYTES = 2 * 1024 * 1024;
  var objectUrl = null;

  function clearPhoto() {
    input.value = '';
    if (objectUrl) URL.revokeObjectURL(objectUrl);
    objectUrl = null;
    preview.hidden = true;
  }

  function showPhoto(file) {
    photoError.hidden = true;
    if (!file) { clearPhoto(); return; }
    var problem = TYPES.indexOf(file.type) === -1 ? 'Only JPG, PNG or WEBP images are allowed.'
      : file.size > MAX_BYTES ? 'Photo is larger than 2 MB. Please choose a smaller image.' : '';
    if (problem) {
      clearPhoto();
      photoError.textContent = problem;
      photoError.hidden = false;
      return;
    }
    if (objectUrl) URL.revokeObjectURL(objectUrl);
    objectUrl = URL.createObjectURL(file);
    previewImage.src = objectUrl;
    previewName.textContent = file.name;
    previewMeta.textContent = Math.round(file.size / 1024) + ' KB';
    preview.hidden = false;
  }

  input.addEventListener('change', function () { showPhoto(input.files[0]); });
  form.querySelector('[data-preview-remove]').addEventListener('click', clearPhoto);

  ['dragenter', 'dragover'].forEach(function (type) {
    dropzone.addEventListener(type, function (event) { event.preventDefault(); dropzone.classList.add('is-over'); });
  });
  ['dragleave', 'drop'].forEach(function (type) {
    dropzone.addEventListener(type, function (event) { event.preventDefault(); dropzone.classList.remove('is-over'); });
  });
  dropzone.addEventListener('drop', function (event) {
    if (event.dataTransfer && event.dataTransfer.files.length) {
      input.files = event.dataTransfer.files;
      showPhoto(input.files[0]);
    }
  });
})();
