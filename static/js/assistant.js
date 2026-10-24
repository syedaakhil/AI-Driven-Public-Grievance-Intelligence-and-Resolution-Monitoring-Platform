/* City Corporation — "Citizen help" assistant widget.
   Sends each message to POST /api/assistant/chat/ and shows the reply. All text from the server is
   inserted with textContent, never as HTML. */
(function () {
  'use strict';

  // Translations from Django's JavaScript catalogue (/jsi18n/); English if it didn't load.
  var gettext = window.gettext || function (text) { return text; };
  var interpolate = window.interpolate || function (text, values) {
    return text.replace(/%\((\w+)\)s/g, function (_, key) { return values[key]; });
  };

  var root = document.querySelector('[data-assistant]');
  if (!root) return;

  var openButton = root.querySelector('[data-assistant-open]');
  var closeButton = root.querySelector('[data-assistant-close]');
  var panel = root.querySelector('#assistant-panel');
  var messages = root.querySelector('[data-assistant-messages]');
  var form = root.querySelector('[data-assistant-form]');
  var input = root.querySelector('[data-assistant-input]');
  var sendButton = root.querySelector('[data-assistant-send]');
  var busy = false;

  function csrfToken() {
    var field = document.querySelector('#logout-form [name="csrfmiddlewaretoken"]');
    return field ? field.value : '';
  }

  function scrollDown() { messages.scrollTop = messages.scrollHeight; }

  function element(tag, className, text) {
    var node = document.createElement(tag);
    if (className) node.className = className;
    if (text !== undefined) node.textContent = text;
    return node;
  }

  function addMessage(kind, text) {
    var bubble = element('div', 'assistant__msg assistant__msg--' + kind);
    bubble.appendChild(element('p', '', text));
    messages.appendChild(bubble);
    scrollDown();
    return bubble;
  }

  function addResults(results) {
    if (!results || !results.length) return;
    var list = element('div', 'assistant__results');
    results.forEach(function (item) {
      var card = element('article', 'assistant__card');
      var top = element('div', 'assistant__card-top');
      top.appendChild(element('span', 'assistant__ref', item.ref + (item.source && item.source !== 'Complaint' ? ' · ' + item.source : '')));
      if (item.status) top.appendChild(element('span', 'assistant__status', item.status));
      card.appendChild(top);
      card.appendChild(element('p', 'assistant__card-title', item.title || gettext('(no title)')));
      var meta = [item.details, item.date].filter(Boolean).join(' · ');
      if (meta) card.appendChild(element('p', 'assistant__card-meta', meta));
      if (item.url) {
        var link = element('a', '', gettext('Open complaint →'));
        link.href = item.url;
        card.appendChild(link);
      }
      list.appendChild(card);
    });
    messages.appendChild(list);
    scrollDown();
  }

  function showTyping() {
    var dots = element('div', 'assistant__typing');
    dots.setAttribute('aria-label', gettext('Assistant is typing'));
    dots.appendChild(element('span'));
    dots.appendChild(element('span'));
    dots.appendChild(element('span'));
    messages.appendChild(dots);
    scrollDown();
    return dots;
  }

  function setBusy(value) {
    busy = value;
    sendButton.disabled = value;
    input.setAttribute('aria-busy', String(value));
  }

  function friendlyError(status, data) {
    if (data && data.error) return data.error;
    if (status === 401 || status === 403) return gettext('Your session has ended. Please sign in again.');
    if (status === 429) return gettext('Too many messages. Please wait a minute and try again.');
    if (status >= 500) return gettext('The server had a problem. Please try again in a moment.');
    return gettext('Something went wrong. Please try again.');
  }

  // `shown` is what appears in the user's bubble (a chip's translated label); `text` is what is sent.
  function send(text, shown) {
    text = (text || '').trim();
    if (!text || busy) return;
    if (text.length > 500) {
      addMessage('error', gettext('Please keep the message under 500 characters.'));
      return;
    }
    addMessage('user', shown || text);
    input.value = '';
    setBusy(true);
    var typing = showTyping();

    fetch(root.getAttribute('data-url'), {
      method: 'POST',
      credentials: 'same-origin',
      headers: { 'Content-Type': 'application/json', 'Accept': 'application/json', 'X-CSRFToken': csrfToken() },
      body: JSON.stringify({ message: text })
    }).then(function (response) {
      return response.json().catch(function () { return null; }).then(function (data) {
        typing.remove();
        if (!response.ok || !data) {
          addMessage('error', friendlyError(response.status, data));
          if (response.status === 401 && data && data.login_url) {
            setTimeout(function () { window.location.href = data.login_url; }, 1500);
          }
          return;
        }
        addMessage('bot', data.reply);
        addResults(data.results);
      });
    }).catch(function () {
      typing.remove();
      addMessage('error', gettext('Could not reach the server. Check your connection and try again.'));
    }).then(function () {
      setBusy(false);
      input.focus();
    });
  }

  function open() {
    panel.hidden = false;
    openButton.setAttribute('aria-expanded', 'true');
    input.focus();
  }

  function close() {
    panel.hidden = true;
    openButton.setAttribute('aria-expanded', 'false');
    openButton.focus();
  }

  openButton.addEventListener('click', open);
  closeButton.addEventListener('click', close);
  root.addEventListener('keydown', function (event) {
    if (event.key === 'Escape' && !panel.hidden) close();
  });
  form.addEventListener('submit', function (event) {
    event.preventDefault();
    send(input.value);
  });
  root.querySelectorAll('[data-assistant-chip]').forEach(function (chip) {
    chip.addEventListener('click', function () { send(chip.getAttribute('data-query') || chip.textContent, chip.textContent); });
  });
})();
