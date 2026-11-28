"""Provider API key lookup: process environment first, then the OS credential store.

A desktop app opened by double-click does not inherit variables exported in a
terminal. ``api_key`` therefore falls back to the operating-system credential
store (Windows Credential Manager, macOS Keychain) through the optional
``keyring`` package. The environment variable named by ``api_key_env`` always
wins, and ``MEETING_COPILOT_KEYRING=0`` disables the fallback entirely.

A saved key belongs to one destination. It is stored under the host it is sent
to (``scope``), so choosing another provider never sends the previous provider's
key there: the new host simply has no key until one is entered. A key saved by an
earlier version without a host is never read.

Run ``python -m meeting_copilot.credentials set --config <config.json>`` to store
a key from a terminal. The key is read from a hidden prompt, never from the
command line, and is never printed.
"""
import argparse
import getpass
import os
import sys

SERVICE = 'Meeting Copilot'
DEFAULT_NAME = 'MEETING_COPILOT_API_KEY'
PROVIDER_HOSTS = {'openai': 'api.openai.com', 'anthropic': 'api.anthropic.com'}
_found = {}


def scope_for(config):
    """The host a configuration sends its key to, or None for the local mode."""
    from urllib.parse import urlsplit

    if config.provider == 'demo':
        return None
    host = urlsplit(config.base_url).hostname if config.base_url else None
    return (host or PROVIDER_HOSTS.get(config.provider) or config.provider).lower()


def _account(name, scope):
    return f'{name}@{scope}' if scope else name


def _keyring():
    if os.environ.get('MEETING_COPILOT_KEYRING', '1') == '0':
        return None
    try:
        import keyring
    except ImportError:
        return None
    return keyring


def store_available():
    return _keyring() is not None


def stored_key(name, scope=None):
    store = _keyring()
    if store is None:
        return None
    try:
        return store.get_password(SERVICE, _account(name, scope)) or None
    except Exception:
        return None


def save_key(name, value, scope=None):
    """Save ``value`` for ``name`` at ``scope``. False when the OS store is unavailable or refuses."""
    store = _keyring()
    if store is None:
        return False
    try:
        store.set_password(SERVICE, _account(name, scope), value)
    except Exception:
        return False
    _found.pop((name, scope), None)
    return True


def delete_key(name, scope=None):
    """Remove the saved key for ``name`` at ``scope``. False when the OS store is unavailable or refuses."""
    store = _keyring()
    if store is None:
        return False
    try:
        if store.get_password(SERVICE, _account(name, scope)):
            store.delete_password(SERVICE, _account(name, scope))
    except Exception:
        return False
    _found.pop((name, scope), None)
    return True


def api_key(name, scope=None):
    """The key for ``name``: the environment variable, else the key saved for ``scope``.

    An environment variable is the user's own statement of which key to use and is not
    tied to a host. A saved key is only returned for the host it was saved for.
    """
    key = os.environ.get(name)
    if key:
        return key
    if os.environ.get('MEETING_COPILOT_KEYRING', '1') == '0' or scope is None:
        return None
    if (name, scope) not in _found:
        key = stored_key(name, scope)
        if not key:
            return None
        _found[(name, scope)] = key
    return _found[(name, scope)]


def main(argv=None):
    parser = argparse.ArgumentParser(prog='python -m meeting_copilot.credentials', description=__doc__.split('\n')[0])
    parser.add_argument('command', choices=['status', 'set', 'delete'])
    parser.add_argument('--config', help='configuration file: its api_key_env names the key and its provider address is the host')
    parser.add_argument('--name', help='credential name, when not taken from --config (default ' + DEFAULT_NAME + ')')
    parser.add_argument('--host', help='host the key is sent to, when not taken from --config (for example api.deepseek.com)')
    args = parser.parse_args(argv)
    name = args.name or DEFAULT_NAME
    scope = args.host.lower() if args.host else None
    if args.config:
        try:
            from meeting_copilot.config import Config
            config = Config.load(args.config)
        except (OSError, TypeError, ValueError):
            print('The configuration file could not be loaded.', file=sys.stderr)
            return 2
        name = args.name or config.api_key_env
        scope = scope or scope_for(config)
    if scope is None:
        print('No provider is chosen yet, so there is no host to save a key for. Choose the provider in the app\'s '
              'Settings (which can also save the key), or pass --host.', file=sys.stderr)
        return 2

    if args.command == 'status':
        in_env = bool(os.environ.get(name))
        in_store = bool(stored_key(name, scope))
        print(f'credential name: {name}')
        print(f'sent to: {scope}')
        print(f'environment variable: {"set" if in_env else "not set"}')
        if store_available():
            print(f'OS credential store: {"saved" if in_store else "not saved"}')
        else:
            print('OS credential store: unavailable (install the "keyring" package)')
        return 0 if in_env or in_store else 3

    if _keyring() is None:
        print('The OS credential store is unavailable. Install the "keyring" package '
              'in this Python environment. The desktop launcher\'s "doctor" command prints how.', file=sys.stderr)
        return 2
    if args.command == 'delete':
        if not delete_key(name, scope):
            print('The saved key could not be removed.', file=sys.stderr)
            return 1
        print(f'Removed the saved key for {name} at {scope}.')
        return 0

    if sys.stdin.isatty():
        value = getpass.getpass(f'API key for {scope} (input is hidden): ')
    else:
        value = sys.stdin.readline()
    value = value.strip()
    if not value:
        print('No key was entered; nothing was saved.', file=sys.stderr)
        return 1
    if not save_key(name, value, scope):
        print('The key could not be saved in the OS credential store.', file=sys.stderr)
        return 1
    print(f'Saved the key for {name} at {scope} in the OS credential store.')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
