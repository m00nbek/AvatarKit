#!/bin/bash
# Downloads the free (CC0) Quaternius packs into ../.assets-source, where
# build-assets.mjs reads them: the animation libraries, and the base characters
# as the skeleton generated bodies are rigged to.
set -euo pipefail

cd "$(dirname "$0")/.."
DEST=.assets-source
mkdir -p "$DEST"
TMP=$(mktemp -d)
trap 'rm -rf "$TMP"' EXIT

# itch.io's free-download flow: ask for a download page, then for the file's CDN URL.
itch() {
  local slug=$1 upload=$2 out=$3 jar="$TMP/$slug.cookies"
  local page csrf url
  page=$(curl -fsS -c "$jar" -b "$jar" -X POST "https://quaternius.itch.io/$slug/download_url" \
    -H "X-Requested-With: XMLHttpRequest" | python3 -c 'import sys,json;print(json.load(sys.stdin)["url"])')
  csrf=$(curl -fsS -c "$jar" -b "$jar" "$page" | grep -oE 'csrf_token" value="[^"]+"' | head -1 | sed -E 's/.*value="([^"]+)"/\1/')
  url=$(curl -fsS -c "$jar" -b "$jar" -X POST "https://quaternius.itch.io/$slug/file/$upload?source=game_download" \
    -H "X-Requested-With: XMLHttpRequest" -H "Referer: $page" --data-urlencode "csrf_token=$csrf" \
    | python3 -c 'import sys,json;print(json.load(sys.stdin)["url"])')
  curl -fsSL "$url" -o "$out"
}

fetch() {
  local name=$1 zip=$TMP/$1.zip
  shift
  if [ -d "$DEST/$name" ]; then
    echo "have  $name"
    return
  fi
  echo "fetch $name"
  "$@" "$zip"
  mkdir -p "$DEST/$name"
  unzip -q "$zip" -d "$DEST/$name"
}

oga() { curl -fsSL "https://opengameart.org/sites/default/files/$1" -o "$2"; }

fetch "Universal Animation Library" oga universal_animation_librarystandard.zip
fetch "Universal Animation Library 2" oga universal_animation_library_2standard.zip
fetch "Universal Base Characters" itch universal-base-characters 15861669
