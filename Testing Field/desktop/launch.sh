#!/bin/bash
# Meeting Copilot launcher for macOS.
# Double-click "Start Meeting Copilot.command" in the project folder, or call a command directly:
#   ./desktop/launch.sh [run|demo|build|package|doctor|set-key|config|test|help]
set -euo pipefail

desktop_dir="$(cd "$(dirname "$0")" && pwd -P)"
project_root="$(cd "$desktop_dir/.." && pwd -P)"
app="$project_root/build/desktop/Meeting Copilot.app"
python="$project_root/.venv/bin/python"
support="$HOME/Library/Application Support/Meeting Copilot"
workspace="${MEETING_COPILOT_DESKTOP_DATA_DIR:-$support/Workspace}"
demo_workspace="$support/Demo Workspace"
config="${MEETING_COPILOT_DESKTOP_CONFIG:-$workspace/config.json}"
action="${1:-run}"

fail() {
    printf '\nMeeting Copilot could not continue: %s\n' "$1" >&2
    printf 'Run "./desktop/launch.sh doctor" to see what is missing.\n' >&2
    exit 1
}

check() { # name ok(0/1) detail [fix]
    if [[ "$2" == 0 ]]; then
        printf '[OK     ] %s: %s\n' "$1" "$3"
    else
        printf '[MISSING] %s: %s\n' "$1" "$3"
        if [[ -n "${4:-}" ]]; then printf '          Fix: %s\n' "$4"; fi
    fi
}

install_command() { # packages
    # Environments created by uv have no pip of their own.
    if command -v uv >/dev/null 2>&1; then
        printf 'uv pip install --python "%s" %s' "$python" "$1"
    else
        printf '"%s" -m pip install %s' "$python" "$1"
    fi
}

has_python() { [[ -x "$python" ]]; }
has_swift() { /usr/bin/xcrun --sdk macosx --find swiftc >/dev/null 2>&1; }
is_built() { [[ -x "$app/Contents/MacOS/MeetingCopilot" ]]; }

is_running() {
    /usr/bin/pgrep -f "$app/Contents/MacOS/MeetingCopilot" >/dev/null 2>&1
}

is_current() {
    # Stale when the backend, shared UI, macOS host or installed packages are newer than the build.
    local marker="$app/Contents/Info.plist" newer packages
    [[ -f "$marker" ]] || return 1
    newer="$(/usr/bin/find "$project_root/src" "$desktop_dir" -type f \
        ! -path "$desktop_dir/windows/*" ! -path '*/__pycache__/*' -newer "$marker" 2>/dev/null | /usr/bin/head -n 1 || true)"
    [[ -z "$newer" ]] || return 1
    for packages in "$project_root"/.venv/lib/python*/site-packages; do
        if [[ -d "$packages" && "$packages" -nt "$marker" ]]; then return 1; fi
    done
    return 0
}

run_python() {
    PYTHONPATH="$project_root/src" "$python" "$@"
}

build() {
    [[ "$(uname -s)" == Darwin ]] || fail 'This launcher is for macOS. On Windows use "Start Meeting Copilot.cmd".'
    has_python || fail "No Python environment was found. From '$project_root' run: python3 -m venv .venv && .venv/bin/python -m pip install -e '.[audio,desktop]'"
    has_swift || fail 'Building the app needs the Xcode Command Line Tools (one-time). Install them with: xcode-select --install'
    if is_running; then fail 'Quit Meeting Copilot before rebuilding it.'; fi
    printf 'Building Meeting Copilot. The first build copies the Python environment and can take a few minutes...\n'
    "$desktop_dir/build-macos.sh"
}

start() { # demo(0/1)
    if ! is_built; then
        build
    elif ! is_current; then
        if has_python && has_swift && ! is_running; then
            printf 'The source changed since the last build. Rebuilding...\n'
            build
        else
            printf 'Warning: the source changed since the last build, but it cannot be rebuilt right now. Opening the existing build.\n' >&2
        fi
    fi
    if [[ "$1" == 1 ]]; then
        # A separate, configuration-free workspace: the sample meeting, local answers, no API key.
        (
            unset MEETING_COPILOT_DESKTOP_CONFIG
            export MEETING_COPILOT_DESKTOP_DATA_DIR="$demo_workspace"
            export MEETING_COPILOT_KEYRING=0
            nohup "$app/Contents/MacOS/MeetingCopilot" >/dev/null 2>&1 &
        )
    else
        /usr/bin/open "$app"
    fi
    printf 'Meeting Copilot is opening.\n'
}

doctor() {
    local problems=0 missing='' required
    printf 'Project:   %s\nWorkspace: %s\n\n' "$project_root" "$workspace"

    if has_python; then
        check 'Python environment' 0 "$python"
        required='meeting_copilot langgraph litellm mcp faster_whisper sounddevice'
        # shellcheck disable=SC2086
        missing="$(run_python -c 'import importlib.util as u,sys;print(" ".join(x for x in sys.argv[1:] if u.find_spec(x) is None))' $required)"
        if [[ -z "$missing" ]]; then
            check 'Python packages' 0 'all present'
        else
            check 'Python packages' 1 "missing $missing" "from '$project_root': $(install_command "-e '.[audio,desktop]'")"
            problems=$((problems + 1))
        fi
        if run_python -c 'import importlib.util as u,sys;sys.exit(u.find_spec("keyring") is None)'; then
            check 'Saved-key support (keyring)' 0 'needed only to save an API key for double-click launches'
        else
            check 'Saved-key support (keyring)' 1 'needed only to save an API key for double-click launches' "$(install_command keyring)"
        fi
    else
        check 'Python environment' 1 'not found' "from '$project_root': python3 -m venv .venv && .venv/bin/python -m pip install -e '.[audio,desktop]'"
        problems=$((problems + 1))
    fi

    if has_swift; then
        check 'Xcode Command Line Tools' 0 'needed only to build or rebuild the app'
    else
        check 'Xcode Command Line Tools' 1 'needed only to build or rebuild the app' 'xcode-select --install'
    fi

    if ! is_built; then
        check 'App build' 1 'not built yet' './desktop/launch.sh build'
        if ! has_swift; then problems=$((problems + 1)); fi
    elif is_current; then
        check 'App build' 0 'up to date'
    else
        check 'App build' 0 'present, but the source changed since it was built (run rebuilds it)'
    fi

    if [[ -f "$config" ]]; then
        check 'Configuration' 0 "$config"
    else
        check 'Configuration' 0 'none; the app uses local sample mode (run "config" to create one for a real model)'
    fi
    if has_python; then
        printf '\nAPI key (only needed for a real model):\n'
        if [[ -f "$config" ]]; then
            run_python -m meeting_copilot.credentials status --config "$config" || true
        else
            run_python -m meeting_copilot.credentials status || true
        fi
    fi
    printf '\n'
    if [[ "$problems" == 0 ]]; then
        printf 'Ready: double-click "Start Meeting Copilot.command".\n'
    else
        printf '%s item(s) above must be fixed before the app can open.\n' "$problems"
        return 1
    fi
}

usage() {
    cat <<'TEXT'
Meeting Copilot launcher

  Double-click "Start Meeting Copilot.command", or run:  ./desktop/launch.sh <command>

  run       Open the app (default). Builds it first when needed.
  demo      Open the app on a separate sample workspace. No API key or configuration.
  build     Build or rebuild Meeting Copilot.app in build/desktop.
  package   Not available on macOS yet: the app is built on each Mac. See desktop/LAUNCH.md.
  doctor    Check everything the app needs and print how to fix what is missing.
  set-key   Optional: save the API key from a terminal. Settings in the app does the same.
  config    Optional: open the workspace config.json. Settings in the app writes the model choice there.
  test      Run the Python tests and the shared desktop UI tests.
  help      Show this text.

The model, its address and the API key are set once in the app under Settings.
No environment variables are required. These remain optional overrides:
  MEETING_COPILOT_DESKTOP_DATA_DIR, MEETING_COPILOT_DESKTOP_CONFIG,
  and the variable named by api_key_env in config.json (it wins over the saved key).
TEXT
}

case "$action" in
    help|-h|--help) usage ;;
    run) start 0 ;;
    demo) start 1 ;;
    build) build ;;
    package)
        printf 'A portable package is only built on Windows (desktop\\launch.cmd package).\n' >&2
        printf 'On macOS the app is built on each Mac: ./desktop/launch.sh build\n' >&2
        exit 2
        ;;
    doctor) doctor ;;
    set-key)
        has_python || fail 'No Python environment was found.'
        if [[ -f "$config" ]]; then
            run_python -m meeting_copilot.credentials set --config "$config"
        else
            run_python -m meeting_copilot.credentials set
        fi
        printf 'Restart Meeting Copilot if it is open.\n'
        ;;
    config)
        target="$workspace/config.json"
        if [[ ! -f "$target" ]]; then
            mkdir -p "$workspace"
            cp "$project_root/config.example.json" "$target"
            printf 'Created %s from config.example.json (local sample mode until you change provider).\n' "$target"
        fi
        printf 'Opening %s. Restart Meeting Copilot after saving.\n' "$target"
        /usr/bin/open -t "$target"
        ;;
    test)
        has_python || fail 'No Python environment was found.'
        cd "$project_root"
        run_python -m pytest -q
        if command -v node >/dev/null 2>&1; then
            node --test tests/test_desktop_audio_ui.mjs
        else
            printf 'Warning: Node.js was not found; the shared desktop UI tests were skipped.\n' >&2
        fi
        ;;
    *)
        usage >&2
        exit 2
        ;;
esac
