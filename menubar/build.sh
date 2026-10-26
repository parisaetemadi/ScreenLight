#!/bin/sh
# Build ScreenLight.app, the menu bar face for the daemon.
#
# Needs only the Xcode Command Line Tools, which also provide the python3 the
# CLI runs on, so anyone who can run screenlight can build this.

set -eu

here=$(cd "$(dirname "$0")" && pwd)
app="$here/ScreenLight.app"

if ! command -v swiftc >/dev/null 2>&1; then
    echo "build.sh: swiftc not found - install the Xcode Command Line Tools:" >&2
    echo "    xcode-select --install" >&2
    exit 1
fi

rm -rf "$app"
mkdir -p "$app/Contents/MacOS"

# No -O. This app sleeps between menu clicks, so optimisation buys nothing and
# costs most of the build time. The first compile is slow regardless - swiftc
# has to build the Cocoa module before it reaches our file - and it prints
# nothing while it does, which looks exactly like a hang.
echo "compiling (the first run takes a minute, and says nothing while it works)..."
swiftc -framework Cocoa -o "$app/Contents/MacOS/ScreenLight" "$here/main.swift"
echo "compiled."

cat > "$app/Contents/Info.plist" <<'PLIST'
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>CFBundleInfoDictionaryVersion</key><string>6.0</string>
    <key>CFBundleName</key><string>ScreenLight</string>
    <key>CFBundleDisplayName</key><string>ScreenLight</string>
    <key>CFBundleIdentifier</key><string>com.screenlight.bar</string>
    <key>CFBundleExecutable</key><string>ScreenLight</string>
    <key>CFBundlePackageType</key><string>APPL</string>
    <key>CFBundleShortVersionString</key><string>1.0</string>
    <key>CFBundleVersion</key><string>1</string>
    <key>LSMinimumSystemVersion</key><string>11.0</string>
    <!-- No Dock icon and no menu bar of its own: it lives only in the status bar. -->
    <key>LSUIElement</key><true/>
</dict>
</plist>
PLIST

plutil -lint "$app/Contents/Info.plist" >/dev/null || {
    echo "build.sh: the Info.plist did not come out valid" >&2
    exit 1
}

# LaunchServices refuses an unsigned bundle on Apple Silicon - `open` reports
# -10825, "incompatible application version", which says nothing about the real
# cause. An ad-hoc signature (the lone "-") is enough; it is not a developer
# certificate and nothing is sent anywhere.
codesign --force --deep --sign - "$app" 2>/dev/null || {
    echo 'build.sh: could not ad-hoc sign the bundle; open may refuse it' >&2
}

echo "built $app"
echo
echo "  open $app          # run it now"
echo "  cp -r $app /Applications/"
echo
echo "To start it at login: System Settings -> General -> Login Items -> +"
