"""Bounded excerpts from retained sources, with original character/line offsets.

Also decides how much of a transcript a question needs. Live transcription cuts speech into
short fragments (a real ten-minute recording gave 134 segments with a median of 58 characters),
so a limit counted in segments can hand the model a few lines of a meeting. Limits here are
therefore measured in characters, and a question about the whole meeting, its opening or its
end gets that part of the transcript instead of keyword hits or the last few fragments.
"""
import math
import re
from collections import Counter

from .store import tokens

# Transcript characters handed over for a whole-meeting or positional question (about 3,500 tokens).
SCOPE_BUDGET = 14000
# A contract's "N recent segments" is read as at least N * RECENT_FLOOR characters of recent speech
# (about a minute each at the density of the recording above). A meeting short enough to fit
# SCOPE_BUDGET is handed over whole: someone catching up on ten minutes needs all ten.
RECENT_FLOOR = 800
# Time windows: retrieval does not depend on where transcription happened to cut a sentence.
# Fragments are grouped into windows of WINDOW_MS that start every WINDOW_STEP_MS (so neighbours
# overlap by a third), a question is matched against whole windows, and the model is given
# passages of consecutive fragments. A quotation is mapped back to the fragment or fragments it
# came from, so every citation still points at a retained segment.
WINDOW_MS = 45000
WINDOW_STEP_MS = 30000
# Characters of matching windows added for a keyword question on a meeting too long to read whole.
WINDOW_BUDGET = 6000
# A passage handed to the model: consecutive fragments, at most this long.
PASSAGE_MS = 45000
PASSAGE_CHARS = 1200
# Fragments further apart than this are not joined into one passage (and not treated as neighbours).
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


def windows(transcript, span_ms=WINDOW_MS, step_ms=WINDOW_STEP_MS):
    """Overlapping time windows over chronological segment rows; each window is a list of rows."""
    if not transcript:
        return []
    starts = [row.get('start_ms') or 0 for row in transcript]
    result = []; clock = starts[0]; first = 0
    while clock <= starts[-1]:
        while first < len(transcript) and starts[first] < clock:
            first += 1
        last = first
        while last < len(transcript) and starts[last] < clock + span_ms:
            last += 1
        if last > first:
            result.append(transcript[first:last])
        clock += step_ms
    return result


def _stem(term):
    for suffix in ('ing', 'ed', 's'):
        if term.endswith(suffix) and len(term) - len(suffix) >= 3 and not term.endswith('ss'):
            term = term[:-len(suffix)]
            break
    # "note" and "notes", "use" and "using" meet at the same stem.
    return term[:-1] if term.endswith('e') and len(term) > 3 else term


def same_word(one, other):
    """A loose match for window scoring: "submit" finds "submission", "students" finds "student".

    Exact matching would need the question to use the speakers' word forms. Short words must
    match exactly after a plural or tense ending is removed; longer ones may share a long prefix.
    """
    one, other = _stem(one), _stem(other)
    if one == other:
        return True
    shorter = min(len(one), len(other))
    if shorter < 5:
        return False
    common = next((n for n, (a, b) in enumerate(zip(one, other)) if a != b), shorter)
    return common >= max(5, shorter - 2)


def window_rows(transcript, question, budget=WINDOW_BUDGET):
    """Rows of the windows that best match the question, in spoken order.

    A window scores by the question terms it contains, rarer terms counting more, so terms that
    transcription spread over several fragments are found together. Overlapping winners merge.
    """
    wanted = terms(question)
    candidates = windows(transcript)
    if not wanted or not candidates:
        return []
    spoken = [terms(' '.join(row['text'] for row in window)) for window in candidates]
    found = [{term for term in wanted if any(same_word(term, word) for word in words)} for words in spoken]
    spread = Counter(term for hit in found for term in hit)
    score = {n: sum(math.log(1 + len(candidates) / spread[term]) for term in hit) for n, hit in enumerate(found) if hit}
    ranked = sorted(score, key=lambda n: (-score[n], n))
    chosen = {}; size = 0
    for n in ranked:
        # A window that matches far less of the question than the best one is noise, not context.
        if score[n] < 0.5 * score[ranked[0]]:
            break
        fresh = [row for row in candidates[n] if row['id'] not in chosen]
        cost = sum(len(row['text']) for row in fresh)
        if chosen and size + cost > budget:
            continue
        for row in fresh:
            chosen[row['id']] = row
        size += cost
    return [row for row in transcript if row['id'] in chosen]


def passages(rows):
    """Group evidence rows into what the model reads.

    Consecutive transcript fragments of one meeting become one passage, identified by the ID of
    its first fragment. Each passage remembers where every fragment sits in its text, so a
    quotation can be mapped back. Other rows (project files, isolated segments) pass through.
    """
    others = [row for row in rows if row['kind'] != 'segment']
    segments = sorted((row for row in rows if row['kind'] == 'segment'),
                      key=lambda row: (str(row.get('meeting')), row.get('start_ms') or 0, row['id']))
    blocks = [{'id': row['id'], 'kind': row['kind'], 'text': row['text'], 'rows': [(row, 0, len(row['text']))]} for row in others]
    current = None
    for row in segments:
        previous = current['rows'][-1][0] if current else None
        joins = (previous is not None and previous.get('meeting') == row.get('meeting')
                 and 0 <= (row.get('start_ms') or 0) - (previous.get('end_ms') or 0) <= CONTEXT_GAP_MS
                 and (row.get('end_ms') or 0) - (current['rows'][0][0].get('start_ms') or 0) <= PASSAGE_MS
                 and len(current['text']) + 1 + len(row['text']) <= PASSAGE_CHARS)
        if joins:
            start = len(current['text']) + 1
            current['text'] += ' ' + row['text']
            current['rows'].append((row, start, start + len(row['text'])))
        else:
            current = {'id': row['id'], 'kind': 'segment', 'text': row['text'], 'rows': [(row, 0, len(row['text']))]}
            blocks.append(current)
    return blocks


def split_quote(block, quote):
    """The quotation as exact pieces of the fragments it runs through, or [] if it is not in the passage."""
    at = block['text'].find(quote)
    if at < 0:
        return []
    end = at + len(quote); pieces = []
    for row, start, stop in block['rows']:
        piece = block['text'][max(at, start):min(end, stop)].strip()
        if min(end, stop) > max(at, start) and len(piece) >= 3:
            pieces.append({'source_id': row['id'], 'quote': piece})
    return pieces


def to_segments(reply, blocks):
    """Rewrite a model reply that cites passages so that it cites the original segments.

    Claims become one claim per fragment the quotation runs through. An inference that cited a
    passage cites the fragments its quotations came from. Anything else is left for the usual
    validation to accept or reject.
    """
    joined = {block['id']: block for block in blocks if len(block['rows']) > 1}
    if not joined or not isinstance(reply, dict):
        return reply
    claims = []; quoted = {}
    for claim in reply.get('claims') if isinstance(reply.get('claims'), list) else []:
        block = joined.get(claim.get('source_id')) if isinstance(claim, dict) else None
        pieces = split_quote(block, claim['quote']) if block and isinstance(claim.get('quote'), str) else []
        if pieces:
            claims += pieces
            quoted.setdefault(block['id'], []).extend(piece['source_id'] for piece in pieces)
        else:
            claims.append(claim)
    inferences = []
    for inference in reply.get('inferences') if isinstance(reply.get('inferences'), list) else []:
        refs = inference.get('source_ids') if isinstance(inference, dict) else None
        if isinstance(refs, list):
            mapped = [rid for ref in refs for rid in (quoted.get(ref, [ref]) if isinstance(ref, int) else [ref])]
            inference = {**inference, 'source_ids': list(dict.fromkeys(mapped))[:8]}
        inferences.append(inference)
    return {**reply, 'claims': claims, 'inferences': inferences}
