"""
Turning a chat message into a search, and scoring how well a record matches it.

Matching is forgiving on purpose: upper/lower case doesn't matter, part of a word matches
("lak" finds "Lakshmi"), and small spelling differences match too ("Laksmi", "Ravvi").
"""
import re
import unicodedata
from dataclasses import asdict, dataclass, field
from difflib import SequenceMatcher

from django.utils.translation import gettext as _

REFERENCE_RE = re.compile(r'\bcc-\d{4}-\d{5,}\b', re.IGNORECASE)
EMAIL_ADDRESS_RE = re.compile(r'[\w.+-]+@[\w-]+(?:\.[\w-]+)+')
# How close two words must be to count as a spelling difference (0-1). 0.8 lets "laksmi" match
# "lakshmi" but not "ravi" match "rani".
FUZZY_RATIO = 0.8
FUZZY_MIN_LENGTH = 4

# Words that say what the user wants, not what to look for.
STOPWORDS = {
    'a', 'about', 'all', 'an', 'and', 'any', 'are', 'by', 'can', 'citizen', 'complain', 'complaint',
    'complaints', 'data', 'detail', 'details', 'find', 'filed', 'for', 'from', 'get', 'give', 'grievance',
    'grievances', 'i', 'in', 'info', 'information', 'is', 'issue', 'issues', 'list', 'look', 'me', 'my',
    'named', 'called', 'need', 'of', 'on', 'person', 'please', 'pls', 'plz', 'record', 'records',
    'regarding', 'related', 'search', 'see', 'show', 'tell', 'the', 'their', 'them', 'these', 'this',
    'those', 'to', 'up', 'want', 'what', 'which', 'who', 'whose', 'with', 'you', 'your', 'kindly', 'just',
    'now', 'some', 'there', 'here', 'it', 'its', 'do', 'does', 'have', 'has', 'was', 'were', 'be',
    # Hindi
    'की', 'का', 'के', 'को', 'से', 'में', 'और', 'है', 'हैं', 'सभी', 'सब', 'मुझे', 'मेरे', 'मेरी', 'मेरा',
    'दिखाओ', 'दिखाएं', 'दिखाइए', 'बताओ', 'बताइए', 'खोजो', 'खोजें', 'ढूंढो', 'शिकायत', 'शिकायतें',
    'शिकायतों', 'सूची', 'डेटा', 'जानकारी', 'कृपया', 'यह', 'ये', 'वह', 'इस', 'इन', 'पर', 'लिए', 'वाले',
    'वाली', 'नाम', 'व्यक्ति', 'नागरिक',
    # Hinglish (Hindi typed in English letters)
    'ki', 'ka', 'ke', 'ko', 'se', 'mein', 'aur', 'hai', 'hain', 'sab', 'sabhi', 'mujhe', 'muje', 'mere',
    'meri', 'mera', 'dikhao', 'batao', 'khojo', 'dhundo', 'shikayat', 'shikayatein', 'kripya', 'wale', 'wali',
}
# Status words a user might type -> Complaint.status values.
STATUS_WORDS = {
    'open': ['submitted', 'assigned', 'in_progress'],
    'pending': ['submitted', 'assigned', 'in_progress'],
    'unresolved': ['submitted', 'assigned', 'in_progress'],
    'submitted': ['submitted'],
    'assigned': ['assigned'],
    'progress': ['in_progress'],
    'ongoing': ['in_progress'],
    'resolved': ['resolved'],
    'closed': ['resolved'],
    'fixed': ['resolved'],
    'solved': ['resolved'],
    'completed': ['resolved'],
    # Hindi / Hinglish
    'खुली': ['submitted', 'assigned', 'in_progress'], 'खुले': ['submitted', 'assigned', 'in_progress'],
    'लंबित': ['submitted', 'assigned', 'in_progress'], 'बाकी': ['submitted', 'assigned', 'in_progress'],
    'khuli': ['submitted', 'assigned', 'in_progress'], 'baki': ['submitted', 'assigned', 'in_progress'],
    'सौंपी': ['assigned'], 'प्रगति': ['in_progress'],
    'हल': ['resolved'], 'सुलझी': ['resolved'], 'सुलझे': ['resolved'], 'बंद': ['resolved'], 'hal': ['resolved'],
}
TODAY_WORDS = {'today', 'todays', 'आज', 'aaj'}
RECENT_WORDS = {'latest', 'recent', 'new', 'newest', 'नवीनतम', 'नई', 'नए', 'हाल', 'ताज़ा', 'naye', 'nayi'}


def _word_char(char):
    # Letters and digits of every script, plus combining marks: Hindi and Tamil vowel signs (ि, ு, ्)
    # are marks, and without them "शिकायत" would be split into "श क यत".
    return char.isalnum() or unicodedata.category(char).startswith('M')


def normalize(text):
    """Lower-case, Latin accents removed, punctuation turned into spaces (letters of every script kept)."""
    text = unicodedata.normalize('NFKD', str(text or '')).casefold()
    # Drop only Latin accents (U+0300-U+036F: "é" -> "e"); other scripts keep their marks.
    text = unicodedata.normalize('NFC', ''.join(char for char in text if not 0x0300 <= ord(char) < 0x0370))
    text = text.replace("'s", '')
    cleaned = ''.join(char if _word_char(char) or char == '-' else ' ' for char in text)
    return re.sub(r'(?<!\S)-|-(?!\S)', ' ', cleaned).strip()


def words_of(text):
    return [word for word in normalize(text).split() if word]


def similar(a, b):
    return SequenceMatcher(None, a, b).ratio()


@dataclass
class Query:
    """What to look for, parsed from a chat message."""

    words: list = field(default_factory=list)
    reference: str = ''
    statuses: list = field(default_factory=list)
    today: bool = False
    recent: bool = False
    text: str = ''

    def is_empty(self):
        return not (self.words or self.reference or self.statuses or self.today or self.recent)

    def describe(self):
        """A short description of the search, in the current language."""
        from grievances.models import Complaint

        parts = []
        if self.reference:
            parts.append(self.reference)
        if self.words:
            parts.append('“' + ' '.join(self.words) + '”')
        if self.statuses:
            labels = [str(Complaint.Status(value).label).lower() for value in sorted(set(self.statuses))]
            parts.append(_('status %(statuses)s') % {'statuses': '/'.join(labels)})
        if self.today:
            parts.append(_('filed today'))
        elif self.recent:
            parts.append(_('most recent'))
        return ', '.join(parts) or _('everything')

    def to_dict(self):
        return asdict(self)

    @classmethod
    def from_dict(cls, data):
        allowed = {key: data[key] for key in cls.__dataclass_fields__ if key in (data or {})}
        return cls(**allowed)


def parse_query(message, extra_stopwords=()):
    """Split a message into a complaint reference, status/time filters and the words to search for."""
    query = Query(text=str(message or '').strip())
    reference = REFERENCE_RE.search(query.text)
    if reference:
        query.reference = reference.group(0).upper()
    ignore = STOPWORDS | set(extra_stopwords)
    # Email addresses are never search words (and never recipients: see brevo.ONLY_RECIPIENT).
    tokens = words_of(EMAIL_ADDRESS_RE.sub(' ', REFERENCE_RE.sub(' ', query.text)))
    for word in tokens:
        if word in STATUS_WORDS:
            query.statuses.extend(STATUS_WORDS[word])
        elif word in TODAY_WORDS:
            query.today = True
        elif word in RECENT_WORDS:
            query.recent = True
        elif word not in ignore:
            query.words.append(word)
    return query


def score(words, *texts):
    """
    Share of the query words found in the texts (0-1). A word counts fully when it appears inside
    the text (partial match), and 0.8 when a word in the text is spelled almost the same.
    """
    if not words:
        return 1.0
    joined = ' '.join(normalize(text) for text in texts if text)
    text_words = set(joined.split())
    found = 0.0
    for word in words:
        if word in joined:
            found += 1
        elif len(word) >= FUZZY_MIN_LENGTH and any(
                abs(len(candidate) - len(word)) <= 2 and similar(word, candidate) >= FUZZY_RATIO
                for candidate in text_words):
            found += 0.8
    return found / len(words)


def good_enough(value, words):
    """Keep a record when at least half of the query words matched (all of them for one word)."""
    needed = 0.8 if len(words) <= 1 else 0.5
    return value >= needed
