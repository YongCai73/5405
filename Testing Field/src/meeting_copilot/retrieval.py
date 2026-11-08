"""Bounded excerpts from retained sources, with original character/line offsets."""
from .store import tokens

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
