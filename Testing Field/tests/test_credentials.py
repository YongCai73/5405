import io
import sys
import types

import pytest

from meeting_copilot import credentials
from meeting_copilot.config import Config
from meeting_copilot.provider import Provider, ProviderError

NAME = 'TEST_CREDENTIAL_NAME'
HOST = 'api.example.invalid'


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


def entry(host=HOST):
    return (credentials.SERVICE, f'{NAME}@{host}')


def test_scope_is_the_host_the_key_is_sent_to():
    assert credentials.scope_for(Config()) is None
    assert credentials.scope_for(Config(provider='openai', base_url='https://API.DeepSeek.com/v1')) == 'api.deepseek.com'
    assert credentials.scope_for(Config(provider='openai')) == 'api.openai.com'
    assert credentials.scope_for(Config(provider='anthropic')) == 'api.anthropic.com'
    assert credentials.scope_for(Config(provider='openai', base_url='http://localhost:11434/v1')) == 'localhost'


def test_environment_variable_wins_over_saved_key(keyring, monkeypatch):
    keyring[entry()] = 'saved-key'
    assert credentials.api_key(NAME, HOST) == 'saved-key'
    monkeypatch.setenv(NAME, 'environment-key')
    assert credentials.api_key(NAME, HOST) == 'environment-key'


def test_a_saved_key_is_never_returned_for_another_host(keyring):
    """Switching provider must not send the previous provider's key to the new one."""
    keyring[entry('api.anthropic.com')] = 'anthropic-key'
    # A key saved by an earlier version, without a host, is not read for any host.
    keyring[(credentials.SERVICE, NAME)] = 'legacy-key'
    assert credentials.api_key(NAME, 'api.anthropic.com') == 'anthropic-key'
    assert credentials.api_key(NAME, 'api.deepseek.com') is None
    assert credentials.api_key(NAME, None) is None and credentials.api_key(NAME) is None

    deepseek = Provider(Config(provider='openai', model='m', base_url='https://api.deepseek.com/v1', send_evidence=True, api_key_env=NAME))
    with pytest.raises(ProviderError, match='missing_api_key'):
        deepseek._key()
    anthropic = Provider(Config(provider='anthropic', model='m', send_evidence=True, api_key_env=NAME))
    assert anthropic._key() == 'anthropic-key'


def test_missing_everywhere_returns_none(keyring):
    assert credentials.api_key(NAME, HOST) is None


def test_opt_out_ignores_saved_key(keyring, monkeypatch):
    keyring[entry()] = 'saved-key'
    assert credentials.api_key(NAME, HOST) == 'saved-key'
    monkeypatch.setenv('MEETING_COPILOT_KEYRING', '0')
    assert credentials.api_key(NAME, HOST) is None
    assert not credentials.store_available()


def test_store_errors_are_treated_as_missing(keyring, monkeypatch):
    def broken(_service, _name):
        raise RuntimeError('locked')
    monkeypatch.setattr(sys.modules['keyring'], 'get_password', broken)
    assert credentials.api_key(NAME, HOST) is None


def test_missing_keyring_package_falls_back_to_environment(monkeypatch):
    monkeypatch.setenv('MEETING_COPILOT_KEYRING', '1')
    monkeypatch.setitem(sys.modules, 'keyring', None)
    monkeypatch.delenv(NAME, raising=False)
    assert credentials.api_key(NAME, HOST) is None
    assert credentials.main(['set', '--name', NAME, '--host', HOST]) == 2
    monkeypatch.setenv(NAME, 'environment-key')
    assert credentials.api_key(NAME, HOST) == 'environment-key'


def test_provider_uses_saved_key_and_reports_missing_key(keyring):
    provider = Provider(Config(provider='openai', model='m', base_url=f'https://{HOST}/v1', send_evidence=True, api_key_env=NAME))
    with pytest.raises(ProviderError, match='missing_api_key'):
        provider._key()
    keyring[entry()] = 'saved-key'
    assert provider._key() == 'saved-key'


def test_cli_set_status_delete_never_prints_the_key(keyring, monkeypatch, capsys, tmp_path):
    config = tmp_path / 'config.json'
    config.write_text('{"provider": "openai", "base_url": "https://%s/v1", "api_key_env": "%s"}' % (HOST, NAME), encoding='utf-8')
    assert credentials.main(['status', '--config', str(config)]) == 3

    monkeypatch.setattr(sys, 'stdin', io.StringIO('  secret-value  \n'))
    assert credentials.main(['set', '--config', str(config)]) == 0
    assert keyring == {entry(): 'secret-value'}
    assert credentials.main(['status', '--config', str(config)]) == 0
    # The same name at another host is a different, empty slot.
    assert credentials.main(['status', '--name', NAME, '--host', 'api.other.invalid']) == 3

    assert credentials.main(['delete', '--config', str(config)]) == 0
    assert keyring == {}
    assert credentials.api_key(NAME, HOST) is None
    assert credentials.main(['delete', '--name', NAME, '--host', HOST]) == 0

    output = capsys.readouterr()
    assert 'secret-value' not in output.out + output.err
    assert NAME in output.out and HOST in output.out


def test_cli_needs_a_host_and_rejects_an_empty_key(keyring, monkeypatch, tmp_path, capsys):
    local = tmp_path / 'config.json'
    local.write_text('{}', encoding='utf-8')
    # The local mode sends a key nowhere, so there is nothing to save it for.
    assert credentials.main(['set', '--config', str(local)]) == 2
    assert credentials.main(['set', '--name', NAME]) == 2
    assert 'Choose the provider' in capsys.readouterr().err
    monkeypatch.setattr(sys, 'stdin', io.StringIO('\n'))
    assert credentials.main(['set', '--name', NAME, '--host', HOST]) == 1
    assert keyring == {}
