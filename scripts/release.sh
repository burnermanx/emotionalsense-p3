#!/bin/sh
# Prepare a release. Does not tag and does not push: it prints the commands.
#
#   scripts/release.sh 0.1.0
#
# Sets project(VERSION) in CMakeLists.txt, moves whatever is under
# "## [Unreleased]" in CHANGELOG.md into a "## [x.y.z] - <date>" section, and
# runs the tests. Creating the tag and publishing the release is your call.
set -eu
root=$(cd "$(dirname "$0")/.." && pwd)
version=${1:-}

case $version in
    [0-9]*.[0-9]*.[0-9]*) ;;
    *) echo "usage: $0 MAJOR.MINOR.PATCH" >&2; exit 2 ;;
esac

cd "$root"
if [ -n "$(git status --porcelain)" ]; then
    echo "working tree is not clean; commit or stash first" >&2
    git status --short >&2
    exit 1
fi

current=$(sed -n 's/^project(emotionalsense VERSION \([0-9.]*\).*/\1/p' CMakeLists.txt)
if [ "$current" = "$version" ] && ! grep -q '^set(EMOTIONALSENSE_VERSION_SUFFIX' CMakeLists.txt; then
    echo "CMakeLists.txt already says $version" >&2
    exit 1
fi

sed -i "s/^project(emotionalsense VERSION [0-9.]*/project(emotionalsense VERSION $version/" CMakeLists.txt
# a release is not a "-dev" tree: drop the suffix (and its comment) the development tree carries
sed -i '/^# A development tree is "/,/^set(EMOTIONALSENSE_VERSION_SUFFIX/d' CMakeLists.txt
sed -i 's/^\(version = \|VERSION = \)"[^"]*"/\1"'"$version"'"/' launcher/es_version.py

today=$(date -u +%Y-%m-%d)
python3 - "$version" "$today" <<'PY'
import re, sys
version, today = sys.argv[1], sys.argv[2]
text = open("CHANGELOG.md", encoding="utf-8").read()
if f"## [{version}]" in text:
    sys.exit(f"CHANGELOG.md already has a section for {version}")
m = re.search(r"^## \[Unreleased\][^\n]*\n(.*?)(?=^## \[|\Z)", text, re.M | re.S)
body = (m.group(1).strip() if m else "")
if not body:
    body = "### Changed\n\n- (nothing recorded yet)\n"
section = f"## [{version}] - {today}\n\n{body}\n\n"
# Keep a Changelog order: Unreleased first, then the newest release.
if m:
    text = text[:m.end()] + section + text[m.end():]
else:
    text = text.rstrip("\n") + "\n\n" + section
open("CHANGELOG.md", "w", encoding="utf-8").write(text)
print(f"CHANGELOG.md: {version} ({today})")
PY

echo "== building"
cmake -B build -G Ninja >/dev/null
ninja -C build >/dev/null
echo "== ctest"
(cd build && ctest -R "raster|emit" --output-on-failure)

if [ -x build/game/EmotionalSense-P3-game ] && [ -d work/data/module ]; then
    echo "== routes"
    sh scripts/check_scripts.sh
else
    echo "== routes skipped (no build/game/EmotionalSense-P3-game or work/data)"
fi

echo
echo "version set to $version. Tag and publish with:"
echo "  git commit -am \"release: $version\""
echo "  git tag -a v$version -m \"emotionalsense $version\""
echo "  git push && git push --tags"
echo
echo "The tag pushes release.yml, which drafts the release with the source"
echo "tarball, the notes and SHA256SUMS. Publishing the draft is your call."
