#!/bin/bash
set -euo pipefail

if [[ $# -gt 1 ]]; then
    printf 'Usage: %s [output-directory]\n' "$0" >&2
    exit 2
fi

desktop_dir="$(cd "$(dirname "$0")" && pwd -P)"
backend_root="$(cd "$desktop_dir/.." && pwd -P)"
output_dir="${1:-$backend_root/build/desktop}"
if [[ "$output_dir" != /* ]]; then
    output_dir="$PWD/$output_dir"
fi
runtime_root="${MEETING_COPILOT_RUNTIME_ROOT:-$HOME/Library/Application Support/Meeting Copilot/Runtime}"
if [[ "$runtime_root" != /* || "$runtime_root" == / ]]; then
    printf 'MEETING_COPILOT_RUNTIME_ROOT must be an absolute directory path.\n' >&2
    exit 2
fi
runtime_root="${runtime_root%/}"

if [[ "$(uname -s)" != Darwin ]]; then
    printf 'Build Meeting Copilot on macOS.\n' >&2
    exit 1
fi
if [[ ! -x "$backend_root/.venv/bin/python" ]]; then
    printf 'Create .venv and install the Python application in %s first. See desktop/README.md.\n' "$backend_root" >&2
    exit 1
fi
for source in main.swift bridge.py minutes.py ui/index.html ui/styles.css ui/app.js; do
    if [[ ! -f "$desktop_dir/$source" ]]; then
        printf 'Missing desktop source %s.\n' "$desktop_dir/$source" >&2
        exit 1
    fi
done

mkdir -p "$output_dir"
output_dir="$(cd "$output_dir" && pwd -P)"
destination="$output_dir/Meeting Copilot.app"
bundle_id='com.meetingcopilot.desktop'
if [[ -e "$destination" || -L "$destination" ]]; then
    if [[ -L "$destination" || ! -d "$destination" ]] ||
        [[ "$(/usr/libexec/PlistBuddy -c 'Print :CFBundleIdentifier' "$destination/Contents/Info.plist" 2>/dev/null)" != "$bundle_id" ]]; then
        printf 'The output already contains a different item at %s.\n' "$destination" >&2
        exit 1
    fi
fi

runtime_parent="$(dirname "$runtime_root")"
mkdir -p "$runtime_parent"
runtime_parent="$(cd "$runtime_parent" && pwd -P)"
runtime_root="$runtime_parent/$(basename "$runtime_root")"
if [[ -e "$runtime_root" || -L "$runtime_root" ]]; then
    if [[ -L "$runtime_root" || ! -d "$runtime_root" || ! -f "$runtime_root/.meeting-copilot-runtime" ]]; then
        printf 'Choose a fresh runtime directory. The build cannot replace %s.\n' "$runtime_root" >&2
        exit 1
    fi
fi

staging="$(mktemp -d "$output_dir/.meeting-copilot.XXXXXX")"
previous="$staging/Previous.app"
runtime_staging=''
runtime_previous=''
runtime_published=false
app_published=false
finished=false
cleanup() {
    if [[ "$finished" == false && "$app_published" == true ]]; then
        mv "$destination" "$staging/Failed.app"
    fi
    if [[ "$finished" == false && "$runtime_published" == true ]]; then
        mv "$runtime_root" "$runtime_staging/Failed"
    fi
    if [[ -d "$previous" && ! -e "$destination" ]]; then
        mv "$previous" "$destination"
    fi
    if [[ -n "$runtime_previous" && -d "$runtime_previous" && ! -e "$runtime_root" ]]; then
        mv "$runtime_previous" "$runtime_root"
    fi
    if [[ -n "$runtime_staging" ]]; then
        rm -rf "$runtime_staging"
    fi
    rm -rf "$staging"
}
trap cleanup EXIT

bundle="$staging/Meeting Copilot.app"
contents="$bundle/Contents"
resources="$contents/Resources"
mkdir -p "$contents/MacOS" "$resources/ui"

target="$(uname -m)-apple-macosx12.0"
/usr/bin/xcrun --sdk macosx swiftc -O -target "$target" \
    -framework AppKit -framework WebKit -framework AVFoundation \
    "$desktop_dir/main.swift" -o "$contents/MacOS/MeetingCopilot"
cp -RX "$desktop_dir/ui/." "$resources/ui/"
printf '%s\n' "$runtime_root" > "$resources/backend-root.txt"

cat > "$contents/Info.plist" <<'PLIST'
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>CFBundleDevelopmentRegion</key><string>en</string>
    <key>CFBundleDisplayName</key><string>Meeting Copilot</string>
    <key>CFBundleExecutable</key><string>MeetingCopilot</string>
    <key>CFBundleIdentifier</key><string>com.meetingcopilot.desktop</string>
    <key>CFBundleInfoDictionaryVersion</key><string>6.0</string>
    <key>CFBundleName</key><string>Meeting Copilot</string>
    <key>CFBundlePackageType</key><string>APPL</string>
    <key>CFBundleShortVersionString</key><string>0.2.0</string>
    <key>CFBundleVersion</key><string>1</string>
    <key>LSApplicationCategoryType</key><string>public.app-category.productivity</string>
    <key>LSMinimumSystemVersion</key><string>12.0</string>
    <key>NSHighResolutionCapable</key><true/>
    <key>NSMicrophoneUsageDescription</key><string>Meeting Copilot uses your microphone to transcribe meetings you choose to record on this Mac.</string>
    <key>NSPrincipalClass</key><string>NSApplication</string>
</dict>
</plist>
PLIST

if [[ -f "$desktop_dir/icon.swift" ]]; then
    iconset="$staging/AppIcon.iconset"
    mkdir "$iconset"
    master="$iconset/icon_512x512@2x.png"
    /usr/bin/xcrun --sdk macosx swift "$desktop_dir/icon.swift" "$master"
    for size in 16 32 128 256 512; do
        /usr/bin/sips -z "$size" "$size" "$master" --out "$iconset/icon_${size}x${size}.png" >/dev/null
        if [[ "$size" -lt 512 ]]; then
            doubled=$((size * 2))
            /usr/bin/sips -z "$doubled" "$doubled" "$master" --out "$iconset/icon_${size}x${size}@2x.png" >/dev/null
        fi
    done
    /usr/bin/iconutil -c icns "$iconset" -o "$resources/AppIcon.icns"
    /usr/libexec/PlistBuddy -c 'Add :CFBundleIconFile string AppIcon.icns' "$contents/Info.plist"
fi

/usr/bin/plutil -lint "$contents/Info.plist" >/dev/null
# Finder metadata on workspace files can make an otherwise valid bundle unsignable.
/usr/bin/xattr -cr "$bundle"
/usr/bin/codesign --sign - "$bundle"
/usr/bin/codesign --verify --strict "$bundle"

runtime_staging="$(mktemp -d "$runtime_parent/.meeting-copilot-runtime.XXXXXX")"
runtime_bundle="$runtime_staging/Runtime"
runtime_previous="$runtime_staging/Previous"
mkdir -p "$runtime_bundle/desktop"
# Clone files where the volume supports it and preserve Python's symlinks.
cp -cR "$backend_root/.venv" "$runtime_bundle/.venv"
cp -cR "$backend_root/src" "$runtime_bundle/src"
cp -X "$desktop_dir/bridge.py" "$desktop_dir/minutes.py" "$runtime_bundle/desktop/"
# An editable install points at the development checkout. The desktop host
# provides the staged src directory on PYTHONPATH, including for recording.
for editable in "$runtime_bundle"/.venv/lib/python*/site-packages/_editable_impl_meeting_copilot_prototype.pth; do
    rm -f "$editable"
done
touch "$runtime_bundle/.meeting-copilot-runtime"
(
    cd "$runtime_bundle"
    PYTHONPATH="$runtime_bundle/src" .venv/bin/python -B -c \
        'from importlib.util import find_spec
for module in ("meeting_copilot", "langgraph", "litellm", "mcp", "faster_whisper", "sounddevice"):
    if find_spec(module) is None:
        raise SystemExit(f"Missing {module} in the runtime. Install the project with the audio extra.")'
)

if [[ -d "$runtime_root" ]]; then
    if /bin/ps -Aww -o command= | /usr/bin/awk -v bridge="$runtime_root/desktop/bridge.py" \
        '/python[0-9.]* -u / && index($0, bridge " --data-dir ") { active = 1 } END { exit !active }'; then
        printf 'Quit Meeting Copilot before updating its runtime at %s.\n' "$runtime_root" >&2
        exit 1
    fi
    mv "$runtime_root" "$runtime_previous"
fi
mv "$runtime_bundle" "$runtime_root"
runtime_published=true
if [[ -d "$destination" ]]; then
    mv "$destination" "$previous"
fi
mv "$bundle" "$destination"
app_published=true
# Documents-backed folders can attach FinderInfo to a newly moved bundle.
if /usr/bin/xattr -p com.apple.FinderInfo "$destination" >/dev/null 2>&1; then
    /usr/bin/xattr -d com.apple.FinderInfo "$destination"
fi
/usr/bin/codesign --verify --strict "$destination"
finished=true
printf '%s\n' "$destination"
