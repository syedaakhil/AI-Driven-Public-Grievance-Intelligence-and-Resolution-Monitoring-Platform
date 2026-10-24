"""
The chatbot's knowledge layer: every place it can search is a Source, registered here.

To make the chatbot search something new later (another table, uploaded documents, ...), write a
Source subclass with a `search(query, limit)` method that returns SearchResult objects, and add
`@register` above it. Nothing else in the chatbot needs to change.
"""
from dataclasses import dataclass
from datetime import datetime, time

from django.db.models import Q
from django.urls import reverse
from django.utils import timezone
from django.utils.translation import gettext

from .search import good_enough, score

# Records looked at for spelling-tolerant matches when the database's own "contains" search
# finds too few. Enough for a city's recent complaints, small enough to stay fast.
FUZZY_SCAN_LIMIT = 2000

SOURCES = []


def register(source_class):
    SOURCES.append(source_class())
    return source_class


@dataclass
class SearchResult:
    source: str          # e.g. "Complaint", "ward-news"
    ref: str             # complaint ID or the record's ID
    title: str
    details: str = ''    # one line: name, area, ...
    status: str = ''
    date: str = ''       # already formatted, India time
    url: str = ''
    score: float = 0.0
    created: datetime = None

    def as_json(self):
        return {key: getattr(self, key) for key in ('source', 'ref', 'title', 'details', 'status', 'date', 'url')}


def local_text(moment):
    return timezone.localtime(moment).strftime('%d %b %Y, %I:%M %p') if moment else ''


def start_of_today():
    now = timezone.localtime()
    return timezone.make_aware(datetime.combine(now.date(), time.min), now.tzinfo)


class Source:
    name = ''

    def search(self, query, limit):
        """Return (results, total_matches) for a search.Query."""
        raise NotImplementedError

    @staticmethod
    def _rank(rows, query, texts_of, to_result):
        ranked = []
        for row in rows:
            value = score(query.words, *texts_of(row))
            if good_enough(value, query.words):
                result = to_result(row)
                result.score = value
                ranked.append(result)
        ranked.sort(key=lambda result: (-result.score, -(result.created.timestamp() if result.created else 0)))
        return ranked


@register
class ComplaintSource(Source):
    """The existing complaints table (grievances.Complaint). Read directly; nothing is copied."""

    name = 'Complaint'

    def search(self, query, limit):
        from grievances.models import Complaint

        rows = Complaint.objects.select_related('category', 'ward')
        if query.reference:
            rows = rows.filter(reference__iexact=query.reference)
        if query.statuses:
            rows = rows.filter(status__in=query.statuses)
        if query.today:
            rows = rows.filter(created_at__gte=start_of_today())
        rows = rows.order_by('-created_at', '-id')

        if not query.words:
            return [self._result(row) for row in rows[:limit]], rows.count()

        contains = Q()
        for word in query.words:
            contains |= (Q(citizen_name__icontains=word) | Q(title__icontains=word) |
                         Q(description__icontains=word) | Q(location__icontains=word) |
                         Q(taluk__icontains=word) | Q(reference__icontains=word))
        candidates = list(rows.filter(contains)[:FUZZY_SCAN_LIMIT])
        if len(candidates) < limit:
            seen = {row.pk for row in candidates}
            candidates += [row for row in rows[:FUZZY_SCAN_LIMIT] if row.pk not in seen]
        ranked = self._rank(candidates, query, self._texts, self._result)
        return ranked[:limit], len(ranked)

    @staticmethod
    def _texts(row):
        return (row.citizen_name, row.title, row.description, row.location, row.taluk, row.reference)

    @staticmethod
    def _result(row):
        taluk = gettext('%(taluk)s taluk') % {'taluk': row.taluk} if row.taluk else ''
        area = ', '.join(part for part in (row.location, taluk) if part)
        details = ' · '.join(part for part in (row.citizen_name, area,
                                               gettext(row.category.name) if row.category else '') if part)
        return SearchResult(
            source='Complaint', ref=row.reference, title=row.title, details=details,
            status=row.get_status_display(), date=local_text(row.created_at),
            url=reverse('grievances:complaint_detail', args=[row.reference]), created=row.created_at,
        )


@register
class IngestedDataSource(Source):
    """Data sent through the ingestion API (assistant.KnowledgeRecord)."""

    name = 'Uploaded data'

    def search(self, query, limit):
        from .models import KnowledgeRecord

        if query.reference or query.statuses:
            return [], 0      # complaint IDs and statuses only exist on complaints
        rows = KnowledgeRecord.objects.all()
        if query.today:
            rows = rows.filter(created_at__gte=start_of_today())
        if not query.words:
            return [self._result(row) for row in rows[:limit]], rows.count()
        contains = Q()
        for word in query.words:
            contains |= Q(search_text__icontains=word)
        candidates = list(rows.filter(contains)[:FUZZY_SCAN_LIMIT])
        if len(candidates) < limit:
            seen = {row.pk for row in candidates}
            candidates += [row for row in rows[:FUZZY_SCAN_LIMIT] if row.pk not in seen]
        ranked = self._rank(candidates, query, lambda row: (row.title, row.content), self._result)
        return ranked[:limit], len(ranked)

    @staticmethod
    def _result(row):
        snippet = row.content if len(row.content) <= 140 else row.content[:137].rsplit(' ', 1)[0] + '…'
        return SearchResult(
            source=row.dataset, ref=row.external_id or f'#{row.pk}', title=row.title or snippet[:80],
            details=snippet if row.title else '', date=local_text(row.created_at), created=row.created_at,
        )


def search_everything(query, limit=10):
    """
    Search every registered source. Returns (results, total) with the best matches first.
    With no search words, the newest records come first instead.
    """
    results, total = [], 0
    for source in SOURCES:
        found, count = source.search(query, limit)
        results += found
        total += count
    if query.words:
        results.sort(key=lambda result: (-result.score, -(result.created.timestamp() if result.created else 0)))
    else:
        results.sort(key=lambda result: -(result.created.timestamp() if result.created else 0))
    return results[:limit], total
