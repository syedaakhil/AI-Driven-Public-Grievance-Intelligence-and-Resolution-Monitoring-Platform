"""
What does a chat message want? Rules, not an AI model: free, offline and predictable.

  "email me the complaints of Ravi"   -> email, search "ravi"
  "mail me this" / "send it to my inbox" -> email, reuse the previous search
  "pothole anna nagar", "CC-2026-00042", "open complaints today" -> search
  "hi", "help" -> help
"""
import re
from dataclasses import dataclass, field

from .search import Query, normalize, parse_query

# Words meaning "email", including common typos. Exact matches only: fuzzy matching here would turn
# "main road" into "mail road".
EMAIL_WORDS = {
    'email', 'emails', 'e-mail', 'emailed', 'emailing', 'mail', 'mails', 'mailed', 'gmail', 'inbox',
    'emial', 'emai', 'emal', 'eamil', 'mial', 'gamil', 'gmial', 'mailbox',
    # Hindi
    'ईमेल', 'इमेल', 'ई-मेल', 'मेल', 'जीमेल',
}
SEND_WORDS = {
    'send', 'sent', 'forward', 'share', 'deliver', 'export', 'dispatch', 'drop', 'shoot',
    # Hindi / Hinglish: "bhejo", "mail karo", "भेजो", "भेज दो"
    'भेजो', 'भेजें', 'भेज', 'भेजिए', 'भेजना', 'bhejo', 'bhej', 'bhejna', 'bhejiye', 'karo', 'kardo', 'करो', 'करें',
}
ME_WORDS = {'me', 'my', 'us', 'our', 'inbox', 'मुझे', 'मेरे', 'मेरी', 'मेरा', 'mujhe', 'muje', 'mere', 'meri', 'mera'}
SEND_PHRASES = re.compile(r'\b(e-?mail|mail|gmail)\s+(me|it|this|that|them|those|these|us)\b')
# Words that only belong to the email request, never to the search.
EMAIL_STOPWORDS = (EMAIL_WORDS | SEND_WORDS | ME_WORDS |
                   {'via', 'through', 'as', 'an', 'address', 'id', 'copy', 'report', 'दो', 'do', 'par', 'पर'})
GREETINGS = {'hi', 'hii', 'hello', 'hey', 'help', 'vanakkam', 'namaste', 'start', 'menu', 'options',
             'नमस्ते', 'नमस्कार', 'मदद', 'सहायता', 'madad'}


@dataclass
class Intent:
    kind: str                       # "email", "search", "help" or "empty"
    query: Query = field(default_factory=Query)


def is_email_request(message):
    text = normalize(message)
    tokens = text.split()
    if not set(tokens) & EMAIL_WORDS:
        return False
    if SEND_PHRASES.search(text):
        return True
    # "send … email", "email … to me", "can you email the list", "I want it in my inbox"
    return bool(set(tokens) & (SEND_WORDS | ME_WORDS)) or tokens[0] in EMAIL_WORDS


def detect_intent(message):
    text = str(message or '').strip()
    tokens = normalize(text).split()
    if not tokens:
        return Intent('empty')
    if is_email_request(text):
        return Intent('email', parse_query(text, extra_stopwords=EMAIL_STOPWORDS))
    if all(token in GREETINGS or token in {'what', 'can', 'you', 'do', 'how', 'use', 'this', 'me', 'चाहिए'}
           for token in tokens) and set(tokens) & (GREETINGS | {'what', 'how'}):
        return Intent('help')
    query = parse_query(text)
    if query.is_empty():
        return Intent('help')
    return Intent('search', query)
