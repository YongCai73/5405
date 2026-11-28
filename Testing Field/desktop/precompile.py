"""Write bytecode for the modules the desktop app loads, so the first question does not wait for it.

Run by package-windows.ps1 with the packaged Python. Loading the model client
imports about three thousand modules; without bytecode that took 19 s on the
build PC, with it 5.5 s. Only modules that are actually imported are compiled,
which keeps the package small. The files are hash-based and unchecked, so they
stay valid after unzipping, which does not preserve exact file times.
"""
import importlib
import py_compile
import sys

# What the bridge, the first question, a recording and Settings load.
PROBES = ('litellm', 'meeting_copilot.assistant', 'meeting_copilot.meta_suggest', 'meeting_copilot.replay',
          'meeting_copilot.audio', 'meeting_copilot.credentials', 'faster_whisper', 'keyring', 'keyring.backends.Windows',
          'langgraph.graph', 'tiktoken', 'pyaudiowpatch')


def main():
    failed = []
    for name in PROBES:
        try:
            importlib.import_module(name)
        except Exception as error:  # A missing optional module must not stop packaging.
            failed.append(f'{name} ({type(error).__name__})')
    root = sys.prefix.lower()
    extra = [path.lower() for path in sys.argv[1:]]
    compiled = 0
    for module in list(sys.modules.values()):
        source = getattr(module, '__file__', None)
        if not source or not source.endswith('.py'):
            continue
        if not source.lower().startswith(root) and not any(source.lower().startswith(path) for path in extra):
            continue
        try:
            py_compile.compile(source, doraise=True, invalidation_mode=py_compile.PycInvalidationMode.UNCHECKED_HASH)
            compiled += 1
        except (py_compile.PyCompileError, OSError):
            pass
    print(f'precompiled {compiled} modules' + (f'; not importable: {", ".join(failed)}' if failed else ''))


if __name__ == '__main__':
    main()
