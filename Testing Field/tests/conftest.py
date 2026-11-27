import pytest
from meeting_copilot.store import Store
from meeting_copilot.config import Config
from meeting_copilot.provider import Provider
from meeting_copilot.assistant import Assistant
from meeting_copilot.trace import Trace

@pytest.fixture
def store(tmp_path):
    s=Store(tmp_path/'runtime'/'evidence.db');s.create('alice','m','Meeting');yield s;s.close()

@pytest.fixture
def event():
    return {'event_id':'e1','stream':'capture','seq':1,'segment_id':'s1','revision':1,'start_ms':100,'end_ms':500,'text':'SQLite was chosen for simplicity.','final':False}

@pytest.fixture
def assistant(store,tmp_path):return Assistant(store,'alice',Provider(Config()),Trace(tmp_path/'trace.jsonl'))
