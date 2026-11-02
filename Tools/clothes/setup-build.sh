#!/bin/bash
# Prepares the build pod (pod.py build): rsync, Node and the Python packages the asset
# build uses. Node, the Python environment and the build's node_modules live on the
# volume (/workspace), so a later pod only reinstalls rsync.
set -euo pipefail

W=/workspace
apt-get update -qq > /dev/null
apt-get install -y -qq rsync > /dev/null

if [ ! -x $W/node/bin/node ]; then
  echo "== Node"
  file=$(curl -fsSL https://nodejs.org/dist/latest-v24.x/SHASUMS256.txt | awk '/linux-x64.tar.xz$/ {print $2}')
  curl -fsSL https://nodejs.org/dist/latest-v24.x/$file | tar -xJ -C $W
  mv $W/${file%.tar.xz} $W/node
fi

if [ ! -x $W/buildenv/bin/python ]; then
  echo "== Python packages"
  python3 -m venv $W/buildenv
  $W/buildenv/bin/pip install -q numpy pillow
fi
echo "== build pod ready: node $($W/node/bin/node --version), $($W/buildenv/bin/python --version)"
