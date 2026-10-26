"""
The news agent: reads a YouTube channel's newest uploads (YouTube Data API v3), keeps the stories
about municipal problems, and pre-fills them as NewsCandidates for staff review. Approved stories
become records of the "news" dataset, labelled with the reviewer's service category.

The keyword match is only a first filter. A reviewer always checks the story and the category,
so nothing reaches the training data without a person approving it.
"""
import json
import logging
import re
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime

from django.conf import settings
from django.core.cache import cache
from django.db import transaction
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from .models import Dataset, DatasetRecord, NewsCandidate
from .providers import ProviderError, clean_text

logger = logging.getLogger(__name__)

API_ROOT = 'https://www.googleapis.com/youtube/v3/'
HTTP_TIMEOUT_SECONDS = 20
PAGE_SIZE = 50                 # the API's maximum per call
SUMMARY_LENGTH = 1000
MATCH_LENGTH = 400             # title + the start of the description; footers repeat channel boilerplate
UPLOADS_CACHE_SECONDS = 24 * 3600
NEWS_DATASET = 'news'

# Service category slug -> words that point to it. Tamil words are matched as stems inside the
# text (Tamil adds endings: குடிநீர், குடிநீரை, குடிநீருக்கு), English ones as whole words.
KEYWORDS = {
    'water': ['குடிநீ', 'குடிதண்ணீ', 'தண்ணீர் தட்டுப்பா', 'தண்ணீர் இல்லா', 'குழாய் உடை', 'குடிநீர் குழாய்',
              'drinking water', 'water supply', 'water scarcity', 'water shortage', 'pipeline burst', 'pipe burst'],
    'roads': ['சாலை சேத', 'சேதமடைந்த சாலை', 'குண்டும் குழியுமா', 'பள்ளம்', 'பள்ளத்', 'சாலை பணி', 'சாலைப் பணி',
              'குழி தோண்ட', 'pothole', 'potholes', 'damaged road', 'road damage', 'bad road', 'road repair'],
    'sewage': ['கழிவுநீ', 'கழிவு நீ', 'சாக்கடை', 'வடிகால்', 'மழைநீர் தேங்', 'மழை நீர் தேங்', 'தேங்கிய', 'தேங்கி நிற்',
               'பாதாள சாக்கடை', 'sewage', 'drainage', 'waterlogging', 'water logging', 'waterlogged', 'stagnant water',
               'overflowing drain'],
    'sanitation': ['குப்பை', 'சுகாதார சீர்கேடு', 'சுகாதாரக் கேடு', 'துர்நாற்ற', 'கொசு', 'garbage', 'waste dump',
                   'solid waste', 'stench', 'unhygienic', 'mosquito'],
}
# Stories with these words are usually about something else (a road accident is not a road repair).
EXCLUDE = {
    'roads': ['விபத்து', 'accident'],
}

# Tamil Nadu districts: (English name, words that name it).
DISTRICTS = [
    ('Chennai', ['சென்னை', 'chennai']), ('Coimbatore', ['கோவை', 'கோயம்புத்தூர்', 'coimbatore']),
    ('Madurai', ['மதுரை', 'madurai']), ('Tiruchirappalli', ['திருச்சி', 'trichy', 'tiruchirappalli']),
    ('Salem', ['சேலம்', 'salem']), ('Tirunelveli', ['நெல்லை', 'திருநெல்வேலி', 'tirunelveli']),
    ('Thoothukudi', ['தூத்துக்குடி', 'thoothukudi', 'tuticorin']), ('Vellore', ['வேலூர்', 'vellore']),
    ('Erode', ['ஈரோடு', 'erode']), ('Tiruppur', ['திருப்பூர்', 'tiruppur']),
    ('Kanchipuram', ['காஞ்சிபுரம்', 'kanchipuram']), ('Chengalpattu', ['செங்கல்பட்டு', 'chengalpattu']),
    ('Tiruvallur', ['திருவள்ளூர்', 'tiruvallur']), ('Cuddalore', ['கடலூர்', 'cuddalore']),
    ('Viluppuram', ['விழுப்புரம்', 'villupuram', 'viluppuram']), ('Thanjavur', ['தஞ்சாவூர்', 'தஞ்சை', 'thanjavur']),
    ('Tiruvarur', ['திருவாரூர்', 'tiruvarur']), ('Nagapattinam', ['நாகப்பட்டினம்', 'நாகை', 'nagapattinam']),
    ('Mayiladuthurai', ['மயிலாடுதுறை', 'mayiladuthurai']), ('Pudukkottai', ['புதுக்கோட்டை', 'pudukkottai']),
    ('Sivaganga', ['சிவகங்கை', 'sivaganga']), ('Ramanathapuram', ['ராமநாதபுரம்', 'ramanathapuram']),
    ('Virudhunagar', ['விருதுநகர்', 'virudhunagar']), ('Theni', ['தேனி', 'theni']),
    ('Dindigul', ['திண்டுக்கல்', 'dindigul']), ('Karur', ['கரூர்', 'karur']), ('Namakkal', ['நாமக்கல்', 'namakkal']),
    ('Dharmapuri', ['தருமபுரி', 'தர்மபுரி', 'dharmapuri']), ('Krishnagiri', ['கிருஷ்ணகிரி', 'krishnagiri']),
    ('Tiruvannamalai', ['திருவண்ணாமலை', 'tiruvannamalai']), ('Kallakurichi', ['கள்ளக்குறிச்சி', 'kallakurichi']),
    ('Ariyalur', ['அரியலூர்', 'ariyalur']), ('Perambalur', ['பெரம்பலூர்', 'perambalur']),
    ('Nilgiris', ['நீலகிரி', 'ஊட்டி', 'nilgiris', 'ooty']), ('Kanniyakumari', ['கன்னியாகுமரி', 'நாகர்கோவில்', 'kanyakumari']),
    ('Tenkasi', ['தென்காசி', 'tenkasi']), ('Ranipet', ['ராணிப்பேட்டை', 'ranipet']),
    ('Tirupattur', ['திருப்பத்தூர்', 'tirupattur']),
]


def _contains(text, word):
    if word.isascii():
        return re.search(rf'\b{re.escape(word)}\b', text) is not None
    return word in text


def match(text):
    """(category slug, [matched words]) for the best-matching category, or (None, []) if none match."""
    text = text.lower()
    best, best_words = None, []
    for slug, words in KEYWORDS.items():
        if any(_contains(text, word) for word in EXCLUDE.get(slug, ())):
            continue
        found = [word for word in words if _contains(text, word)]
        if len(found) > len(best_words):
            best, best_words = slug, found
    return best, best_words


def guess_district(text):
    text = text.lower()
    for name, words in DISTRICTS:
        if any(_contains(text, word) for word in words):
            return name
    return ''


# ---------- YouTube Data API v3 ----------

def _get(endpoint, **params):
    params['key'] = settings.YOUTUBE_API_KEY
    url = API_ROOT + endpoint + '?' + urllib.parse.urlencode(params)
    request = urllib.request.Request(url, headers={'Accept': 'application/json'})
    try:
        with urllib.request.urlopen(request, timeout=HTTP_TIMEOUT_SECONDS) as response:
            return json.loads(response.read())
    except urllib.error.HTTPError as exc:
        reason = _api_reason(exc)
        # Never log the URL: it holds the API key.
        logger.warning('YouTube API %s answered HTTP %s (%s)', endpoint, exc.code, reason)
        if reason in ('quotaExceeded', 'dailyLimitExceeded', 'rateLimitExceeded'):
            raise ProviderError(_('The YouTube API daily quota is used up. Try again tomorrow.')) from None
        if reason in ('keyInvalid', 'badRequest') or exc.code in (400, 401, 403):
            raise ProviderError(_('YouTube refused the API key. Check YOUTUBE_API_KEY in .env.')) from None
        raise ProviderError(_('YouTube answered with HTTP %(code)s.') % {'code': exc.code},
                            temporary=exc.code >= 500) from None
    except (urllib.error.URLError, TimeoutError, OSError):
        raise ProviderError(_('YouTube could not be reached.'), temporary=True) from None
    except (ValueError, UnicodeDecodeError):
        raise ProviderError(_('YouTube sent an answer that could not be read.'), temporary=True) from None


def _api_reason(error):
    try:
        body = json.loads(error.read() or b'{}')
        return body['error']['errors'][0]['reason']
    except (ValueError, KeyError, IndexError, TypeError, AttributeError):
        return ''


def uploads_playlist(handle):
    """The channel's "uploads" playlist ID (1 quota unit, cached for a day)."""
    cache_key = f'mlops:youtube-uploads:{handle.lower()}'
    playlist = cache.get(cache_key)
    if playlist:
        return playlist
    body = _get('channels', part='contentDetails', forHandle=handle)
    try:
        playlist = body['items'][0]['contentDetails']['relatedPlaylists']['uploads']
    except (KeyError, IndexError, TypeError):
        raise ProviderError(_('No YouTube channel was found for %(handle)s. Check YOUTUBE_CHANNEL_HANDLE.')
                            % {'handle': handle}) from None
    cache.set(cache_key, playlist, UPLOADS_CACHE_SECONDS)
    return playlist


def latest_videos(handle, limit):
    """The newest uploads as dicts: id, title, description, published_at (1 quota unit per 50)."""
    playlist = uploads_playlist(handle)
    videos, page_token = [], ''
    while len(videos) < limit:
        params = {'part': 'snippet', 'playlistId': playlist, 'maxResults': PAGE_SIZE}
        if page_token:
            params['pageToken'] = page_token
        body = _get('playlistItems', **params)
        for item in body.get('items', []):
            snippet = item.get('snippet') or {}
            video_id = (snippet.get('resourceId') or {}).get('videoId')
            title = snippet.get('title') or ''
            if not video_id or title in ('Private video', 'Deleted video'):
                continue
            videos.append({'id': video_id, 'title': title, 'description': snippet.get('description') or '',
                           'published_at': _parse_time(snippet.get('publishedAt'))})
        page_token = body.get('nextPageToken')
        if not page_token:
            break
    return videos[:limit]


def _parse_time(value):
    try:
        return datetime.fromisoformat(value.replace('Z', '+00:00')) if value else None
    except ValueError:
        return None


def collect_candidates():
    """Fetch the newest videos and save the municipal ones as pending NewsCandidates. Returns how many are new."""
    from grievances.models import Category

    if not settings.YOUTUBE_API_KEY:
        raise ProviderError(_('Add YOUTUBE_API_KEY to .env to collect news (see ML_SETUP.md).'))
    categories = {category.slug: category for category in Category.objects.all()}
    found_new = 0
    for video in latest_videos(settings.YOUTUBE_CHANNEL_HANDLE, settings.YOUTUBE_MAX_VIDEOS):
        description = clean_text(video['description'])
        title = clean_text(video['title'])[:300]
        slug, words = match(f'{title} {description[:MATCH_LENGTH]}')
        if slug is None or slug not in categories:
            continue
        _candidate, created = NewsCandidate.objects.get_or_create(
            source='youtube', external_id=video['id'][:64],
            defaults={
                'title': title,
                'summary': description[:SUMMARY_LENGTH],
                'url': f'https://www.youtube.com/watch?v={urllib.parse.quote(video["id"])}',
                'published_at': video['published_at'],
                'suggested_category': categories[slug],
                'category': categories[slug],
                'district': guess_district(f'{title} {description}'),
                'matched_keywords': ', '.join(words)[:300],
            })
        found_new += created
    return found_new


# ---------- Review ----------

def approve(candidate, category, district, user):
    """Approve a pending story and add it to the "news" dataset straight away."""
    with transaction.atomic():
        candidate.status = NewsCandidate.Status.APPROVED
        candidate.category = category
        candidate.district = district
        candidate.reviewed_by = user
        candidate.reviewed_at = timezone.now()
        candidate.save()
        dataset, _created = Dataset.objects.get_or_create(
            slug=NEWS_DATASET, defaults={'name': str(settings.YOUTUBE_SOURCE_NAME)[:120]})
        DatasetRecord.objects.update_or_create(
            dataset=dataset, source_id=f'{candidate.source}:{candidate.external_id}',
            defaults={'text': candidate.record_text, 'label': category.slug, 'collected_at': timezone.now()})
        refresh_counts(dataset)


def reject(candidate, user):
    candidate.status = NewsCandidate.Status.REJECTED
    candidate.reviewed_by = user
    candidate.reviewed_at = timezone.now()
    candidate.save(update_fields=['status', 'reviewed_by', 'reviewed_at'])


def refresh_counts(dataset):
    dataset.record_count = dataset.records.count()
    dataset.label_count = dataset.records.exclude(label='').values('label').distinct().count()
    dataset.save(update_fields=['record_count', 'label_count'])


def approved_records():
    """The "news" dataset: every approved story, labelled with its reviewed category."""
    from .providers import CollectedRecord

    rows = (NewsCandidate.objects.filter(status=NewsCandidate.Status.APPROVED, category__isnull=False)
            .select_related('category').order_by('pk'))
    return [CollectedRecord(f'{row.source}:{row.external_id}', row.record_text, row.category.slug) for row in rows]
