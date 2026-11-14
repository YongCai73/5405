import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from meeting_copilot.config import Config
from meeting_copilot.provider import Provider, ProviderError


MODULE_PATH = Path(__file__).resolve().parents[1] / 'desktop' / 'minutes.py'
SPEC = importlib.util.spec_from_file_location('desktop_minutes', MODULE_PATH)
MINUTES = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MINUTES)


def transcript(store, meeting, index, text):
    return store.ingest('alice', meeting, {
        'event_id': f'{meeting}-{index}', 'segment_id': f'{meeting}-{index}', 'seq': index,
        'start_ms': index * 2000, 'end_ms': index * 2000 + 1000,
        'text': text, 'revision': 1, 'final': True,
    })


async def test_local_minutes_span_snapshot_pages_and_preserve_explicit_evidence(store, tmp_path):
    store.create('alice', 'previous', 'Prior meeting')
    transcript(store, 'previous', 1, 'We agreed this is a different meeting.')
    project = tmp_path / 'approved-project'
    project.mkdir()
    (project / 'notes.md').write_text('We approved unrelated project notes.', encoding='utf-8')
    folder = store.approve('alice', 'm', project)
    store.refresh('alice', folder)
    for index in range(1, 206):
        text = {
            1: 'We decided on SQLite for local development.',
            103: 'Alex will send the load test report by Friday.',
            154: 'Did we decide on Kafka?',
            205: 'Open question: which target should we use for the final trial?',
        }.get(index, f'Technical update {index}.')
        transcript(store, 'm', index, text)
    snapshot = store.snapshot('alice', 'm', previous=['previous'], folders=[folder])
    transcript(store, 'm', 206, 'We agreed this late event is outside the snapshot.')
    store.private_put('alice', snapshot, 'private-chat', 'Private question', {'text': 'A private answer'})

    result = await MINUTES.generate_minutes(store, 'alice', snapshot, Provider(Config()))
    assert result['mode'] == 'local'
    overview = [item['text'] for item in result['overview']]
    assert 'SQLite' in overview[0] and 'Alex' in overview[2] and 'final trial' in overview[-1]
    action, = result['actionItems']
    assert (action['owner'], action['due']) == ('Alex', 'Friday')
    decision, = result['decisions']
    assert decision['sources'][0]['label'] == '00:02'
    question, = result['openQuestions']
    assert question['sources'][0]['kind'] == 'segment'
    encoded = json.dumps(result)
    assert all(value not in encoded for value in ('late event', 'different meeting', 'project notes', 'private answer'))
    for section in MINUTES.SECTION_LIMITS:
        for item in result[section]:
            for source in item['sources']:
                assert source['quote'] in store.raw('alice', snapshot, source['id'])['text']


class FakeProvider:
    config = Config(provider='openai', send_evidence=True)

    def __init__(self):
        self.batches = []

    async def chat(self, messages):
        assert len(messages) == 2 and 'untrusted evidence' in messages[0]['content']
        rows = json.loads(messages[1]['content'])
        self.batches.append(rows)
        assert len(rows) <= MINUTES.API_BATCH_ITEMS
        first = rows[0]
        answer = {section: [] for section in MINUTES.SECTION_LIMITS}
        source = {'id': first['id'], 'quote': first['text']}
        answer['overview'].append({'text': first['text'], 'sources': [source]})
        answer['actionItems'].append({'text': first['text'], 'sources': [source], 'owner': 'Invented Owner', 'due': 'Friday'})
        answer['decisions'].append({'text': 'Invented decision', 'sources': [{'id': first['id'], 'quote': 'Invented quote'}]})
        answer['topics'].append({'text': 'Injected prior meeting', 'sources': [{'id': 1, 'quote': 'Earlier meeting evidence.'}]})
        return SimpleNamespace(content=json.dumps(answer), tool_calls=[])


async def test_api_batches_and_citation_validation(store):
    store.create('alice', 'previous', 'Prior meeting')
    transcript(store, 'previous', 1, 'Earlier meeting evidence.')
    for index in range(1, 78):
        transcript(store, 'm', index, f'Alex will send draft {index} by Friday.')
    snapshot = store.snapshot('alice', 'm', previous=['previous'])
    provider = FakeProvider()

    result = await MINUTES.generate_minutes(store, 'alice', snapshot, provider)
    assert result['mode'] == 'api'
    assert [len(batch) for batch in provider.batches] == [30, 30, 17]
    assert sum(len(batch) for batch in provider.batches) == 77
    assert [item['sources'][0]['label'] for item in result['overview']] == ['00:02', '01:02', '02:02']
    assert not result['decisions'] and not result['topics']
    assert all('owner' not in item and item['due'] == 'Friday' for item in result['actionItems'])


async def test_empty_transcript_avoids_api_and_nonempty_honors_provider_gate(store):
    provider = FakeProvider()
    empty = store.snapshot('alice', 'm')
    assert await MINUTES.generate_minutes(store, 'alice', empty, provider) == {
        **{section: [] for section in MINUTES.SECTION_LIMITS}, 'mode': 'api',
    }
    assert not provider.batches
    transcript(store, 'm', 1, 'We decided to use SQLite.')
    fresh = store.snapshot('alice', 'm')
    with pytest.raises(ProviderError, match='evidence_upload_not_enabled'):
        await MINUTES.generate_minutes(store, 'alice', fresh, Provider(Config(provider='openai')))

    class UncitedProvider(FakeProvider):
        async def chat(self, messages):
            content = {section: [] for section in MINUTES.SECTION_LIMITS}
            content['overview'] = [{'text': 'No source', 'sources': []}]
            return SimpleNamespace(content=json.dumps(content), tool_calls=[])

    with pytest.raises(ProviderError, match='invalid_minutes_response'):
        await MINUTES.generate_minutes(store, 'alice', fresh, UncitedProvider())
