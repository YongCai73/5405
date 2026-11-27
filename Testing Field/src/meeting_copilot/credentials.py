"""Provider API key lookup: process environment first, then the OS credential store.

A desktop app opened by double-click does not inherit variables exported in a
terminal. ``api_key`` therefore falls back to the operating-system credential
store (Windows Credential Manager, macOS Keychain) through the optional
``keyring`` package. The environment variable named by ``api_key_env`` always
wins, and ``MEETING_COPILOT_KEYRING=0`` disables the fallback entirely.

Run ``python -m meeting_copilot.credentials set`` to store a key once. The key
is read from a hidden prompt, never from the command line, and is never printed.
"""
import argparse
import getpass
import os
import sys

SERVICE = 'Meeting Copilot'
DEFAULT_NAME = 'MEETING_COPILOT_API_KEY'
_found = {}


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


def stored_key(name):
    store = _keyring()
    if store is None:
        return None
    try:
        return store.get_password(SERVICE, name) or None
    except Exception:
        return None


def api_key(name):
    """Return the key for ``name`` from the environment, else the OS store."""
    key = os.environ.get(name)
    if key:
        return key
    if os.environ.get('MEETING_COPILOT_KEYRING', '1') == '0':
        return None
    if name not in _found:
        key = stored_key(name)
        if not key:
            return None
        _found[name] = key
    return _found[name]


def _name(args):
    if args.name:
        return args.name
    if args.config:
        from meeting_copilot.config import Config
        return Config.load(args.config).api_key_env
    return DEFAULT_NAME


def main(argv=None):
    parser = argparse.ArgumentParser(prog='python -m meeting_copilot.credentials', description=__doc__.split('\n')[0])
    parser.add_argument('command', choices=['status', 'set', 'delete'])
    parser.add_argument('--name', help='credential name; defaults to api_key_env from --config, else ' + DEFAULT_NAME)
    parser.add_argument('--config', help='configuration file whose api_key_env names the credential')
    args = parser.parse_args(argv)
    try:
        name = _name(args)
    except (OSError, TypeError, ValueError):
        print('The configuration file could not be loaded.', file=sys.stderr)
        return 2

    if args.command == 'status':
        in_env = bool(os.environ.get(name))
        in_store = bool(stored_key(name))
        print(f'credential name: {name}')
        print(f'environment variable: {"set" if in_env else "not set"}')
        if store_available():
            print(f'OS credential store: {"saved" if in_store else "not saved"}')
        else:
            print('OS credential store: unavailable (install the "keyring" package)')
        return 0 if in_env or in_store else 3

    store = _keyring()
    if store is None:
        print('The OS credential store is unavailable. Install the "keyring" package '
              'in this Python environment. The desktop launcher\'s "doctor" command prints how.', file=sys.stderr)
        return 2
    if args.command == 'delete':
        try:
            if store.get_password(SERVICE, name):
                store.delete_password(SERVICE, name)
        except Exception:
            print('The saved key could not be removed.', file=sys.stderr)
            return 1
        _found.pop(name, None)
        print(f'Removed the saved key for {name}.')
        return 0

    if sys.stdin.isatty():
        value = getpass.getpass(f'API key for {name} (input is hidden): ')
    else:
        value = sys.stdin.readline()
    value = value.strip()
    if not value:
        print('No key was entered; nothing was saved.', file=sys.stderr)
        return 1
    try:
        store.set_password(SERVICE, name, value)
    except Exception:
        print('The key could not be saved in the OS credential store.', file=sys.stderr)
        return 1
    _found.pop(name, None)
    print(f'Saved the key for {name} in the OS credential store.')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
