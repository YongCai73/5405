"""Bounded excerpts from retained sources, with original character/line offsets.

Also decides how much of a transcript a question needs. Live transcription cuts speech into
short fragments (a real ten-minute recording gave 134 segments with a median of 58 characters),
so a limit counted in segments can hand the model a few lines of a meeting. Limits here are
therefore measured in characters, and a question about the whole meeting, its opening or its
end gets that part of the transcript instead of keyword hits or the last few fragments.
"""
import re

from .store import tokens

# Transcript characters handed over for a whole-meeting or positional question (about 3,500 tokens).
SCOPE_BUDGET = 14000
# A contract's "N recent segments" is read as at least N * RECENT_FLOOR characters of recent speech
# (about a minute each at the density of the recording above). A meeting short enough to fit
# SCOPE_BUDGET is handed over whole: someone catching up on ten minutes needs all ten.
RECENT_FLOOR = 800
# A keyword hit on a fragment brings along neighbours spoken within this gap, for context.
CONTEXT_GAP_MS = 3000
CONTEXT_EACH_SIDE = 2
SHORT_SEGMENT = 200

_OPENING = ('the beginning', 'at the start', 'the start of', 'opening of', 'opening remarks', 'opening part', 'first part',
            'first few minutes', 'first minutes', 'how did the meeting start', 'how did it start', 'kicked off', 'started with')
_CLOSING = ('at the end', 'the end of', 'closing remarks', 'closing part', 'last part', 'last few minutes', 'last minutes',
            'how did the meeting end', 'how did it end', 'ended with')
_OVERVIEW = ('whole meeting', 'entire meeting', 'full meeting', 'meeting overview', 'main points', 'main point', 'main content',
             'main topics', 'main topic', 'key points', 'key takeaways', 'what was discussed', 'what did they talk about',
             'what was covered', 'what was it about', 'what is it about', 'meeting about', 'seminar about', 'lecture about')
_SUMMARY = re.compile(r'\b(summari[sz]e|summary|recap|overview|explain|describe|outline|gist)\b')
_MEETING = re.compile(r'\b(meeting|seminar|lecture|talk|session|call|class|presentation)\b')


def time_scope(question):
    """'opening', 'closing' or 'overview' when the question is about that part of the meeting, else None."""
    text = ' '.join(question.lower().split())
    if any(cue in text for cue in _OPENING):
        return 'opening'
    if any(cue in text for cue in _CLOSING):
        return 'closing'
    if any(cue in text for cue in _OVERVIEW) or (_SUMMARY.search(text) and _MEETING.search(text)):
        return 'overview'
    return None


def _take(rows, limit):
    taken = []; size = 0
    for row in rows:
        if size + len(row['text']) > limit:
            break
        taken.append(row); size += len(row['text'])
    return taken


def scoped_rows(rows, scope, budget=SCOPE_BUDGET):
    """Chronological transcript rows for a scope, and a description of what they cover.

    A transcript that fits the budget is handed over whole, whatever the scope: the model
    then sees the opening in context. A longer one is cut to the opening or closing part,
    or to eight evenly spaced stretches for an overview.
    """
    rows = list(rows)
    if sum(len(row['text']) for row in rows) <= budget:
        return rows, 'the whole meeting'
    if scope == 'opening':
        return _take(rows, budget // 2), 'the opening part of the meeting only'
    if scope == 'closing':
        return _take(rows[::-1], budget // 2)[::-1], 'the closing part of the meeting only'
    chosen = []; seen = set(); parts = 8
    for part in range(parts):
        for row in _take(rows[part * len(rows) // parts:], budget // parts):
            if row['id'] not in seen:
                seen.add(row['id']); chosen.append(row)
    return chosen, 'eight evenly spaced stretches of the meeting, with gaps between them'


def older_rows(selected, transcript, floor):
    """Rows just before `selected` (the most recent segments) until together they reach `floor` characters."""
    if not selected:
        return []
    size = sum(len(row['text']) for row in selected)
    have = {row['id'] for row in selected}
    first = next((n for n, row in enumerate(transcript) if row['id'] in have), 0)
    extra = []
    for row in reversed(transcript[:first]):
        if size >= floor:
            break
        extra.append(row); size += len(row['text'])
    return extra[::-1]


def context_rows(hits, transcript, have):
    """Neighbours of short segment hits that were spoken right before or after them."""
    position = {row['id']: n for n, row in enumerate(transcript)}
    extra = []; seen = set(have)
    for hit in hits:
        index = position.get(hit['id'])
        if index is None or len(hit['text']) >= SHORT_SEGMENT:
            continue
        for step in (-1, 1):
            previous = transcript[index]
            for offset in range(1, CONTEXT_EACH_SIDE + 1):
                at = index + step * offset
                if not 0 <= at < len(transcript):
                    break
                row = transcript[at]
                earlier, later = (row, previous) if step < 0 else (previous, row)
                if (later.get('start_ms') or 0) - (earlier.get('end_ms') or 0) > CONTEXT_GAP_MS:
                    break
                if row['id'] not in seen:
                    seen.add(row['id']); extra.append(row)
                previous = row
    return extra

STOP = set('a an the is are was were we why what which how when who did do does for from to of in on and or it its this that our have has can about please tell me with use using'.split())


def terms(text):
    return set(tokens(text)) - STOP


def excerpt(row, question, budget=2400):
    """Scan the complete bounded source, then return its best contiguous window.

    Overlap keeps an identifier/sentence at a window boundary retrievable. Original
    offsets are retained; the hash always identifies the entire retained revision.
    """
    text = row['text']
    q = terms(question)
    starts = range(0, max(1, len(text)), budget - 400)
    start = max(starts, key=lambda n: (len(q & terms(text[n:n+budget])), -n))
    original_start=start
    original_score=len(q & terms(text[start:start+budget]))
    # Prefer complete lines only if doing so preserves all matched query terms.
    line = text.rfind('\n', max(0, start-200), start)
    if line >= 0:
        start = line + 1
    end = min(len(text), start + budget)
    if end < len(text):
        newline = text.rfind('\n', start + budget//2, end)
        if newline >= 0:
            end = newline
    if len(q & terms(text[start:end]))<original_score:
        start=original_start;end=min(len(text),start+budget)
    return {**row, 'text': text[start:end], 'text_offset': start,
            'line_offset': text[:start].count('\n'), 'source_length': len(text)}
