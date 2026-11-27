import io
import sys
import types

import pytest

from meeting_copilot import credentials
from meeting_copilot.config import Config
from meeting_copilot.provider import Provider, ProviderError

NAME = 'TEST_CREDENTIAL_NAME'


@pytest.fixture
def keyring(monkeypatch):
    """An in-memory stand-in for the OS credential store."""
    saved = {}
    fake = types.ModuleType('keyring')
    fake.get_password = lambda service, name: saved.get((service, name))
    fake.set_password = lambda service, name, value: saved.__setitem__((service, name), value)
    fake.delete_password = lambda service, name: saved.pop((service, name))
    monkeypatch.setitem(sys.modules, 'keyring', fake)
    monkeypatch.setenv('MEETING_COPILOT_KEYRING', '1')
    monkeypatch.delenv(NAME, raising=False)
    monkeypatch.setattr(credentials, '_found', {})
    return saved


def test_environment_variable_wins_over_saved_key(keyring, monkeypatch):
    keyring[(credentials.SERVICE, NAME)] = 'saved-key'
    assert credentials.api_key(NAME) == 'saved-key'
    monkeypatch.setenv(NAME, 'environment-key')
    assert credentials.api_key(NAME) == 'environment-key'


def test_missing_everywhere_returns_none(keyring):
    assert credentials.api_key(NAME) is None


def test_opt_out_ignores_saved_key(keyring, monkeypatch):
    keyring[(credentials.SERVICE, NAME)] = 'saved-key'
    assert credentials.api_key(NAME) == 'saved-key'
    monkeypatch.setenv('MEETING_COPILOT_KEYRING', '0')
    assert credentials.api_key(NAME) is None
    assert not credentials.store_available()


def test_store_errors_are_treated_as_missing(keyring, monkeypatch):
    def broken(_service, _name):
        raise RuntimeError('locked')
    monkeypatch.setattr(sys.modules['keyring'], 'get_password', broken)
    assert credentials.api_key(NAME) is None


def test_missing_keyring_package_falls_back_to_environment(monkeypatch):
    monkeypatch.setenv('MEETING_COPILOT_KEYRING', '1')
    monkeypatch.setitem(sys.modules, 'keyring', None)
    monkeypatch.delenv(NAME, raising=False)
    assert credentials.api_key(NAME) is None
    assert credentials.main(['set', '--name', NAME]) == 2
    monkeypatch.setenv(NAME, 'environment-key')
    assert credentials.api_key(NAME) == 'environment-key'


def test_provider_uses_saved_key_and_reports_missing_key(keyring):
    provider = Provider(Config(provider='openai', model='m', send_evidence=True, api_key_env=NAME))
    with pytest.raises(ProviderError, match='missing_api_key'):
        provider._client()
    keyring[(credentials.SERVICE, NAME)] = 'saved-key'
    assert provider._client()[1] == 'saved-key'


def test_cli_set_status_delete_never_prints_the_key(keyring, monkeypatch, capsys, tmp_path):
    config = tmp_path / 'config.json'
    config.write_text('{"api_key_env": "%s"}' % NAME, encoding='utf-8')
    assert credentials.main(['status', '--config', str(config)]) == 3

    monkeypatch.setattr(sys, 'stdin', io.StringIO('  secret-value  \n'))
    assert credentials.main(['set', '--config', str(config)]) == 0
    assert keyring[(credentials.SERVICE, NAME)] == 'secret-value'
    assert credentials.main(['status', '--config', str(config)]) == 0

    assert credentials.main(['delete', '--config', str(config)]) == 0
    assert keyring == {}
    assert credentials.api_key(NAME) is None
    assert credentials.main(['delete', '--name', NAME]) == 0

    output = capsys.readouterr()
    assert 'secret-value' not in output.out + output.err
    assert NAME in output.out


def test_cli_rejects_an_empty_key(keyring, monkeypatch):
    monkeypatch.setattr(sys, 'stdin', io.StringIO('\n'))
    assert credentials.main(['set', '--name', NAME]) == 1
    assert keyring == {}
