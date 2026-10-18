#!/usr/bin/env bash
# Builds what every agent shares on the VM: the sandbox and proxy images, the internal network, the package cache
# and the host venv. Run as root from the repo copy. Safe to run again. The root setup.sh runs it first.
# LEGION_SANDBOX_DOCKERFILE swaps the sandbox image for one built on it, for a repository that needs Go, Python or more.
set -euo pipefail
cd "$(dirname "$0")"
source ../paths.sh

# The settings shape two files of the images: the deny list of the guarded paths and the hosts the proxy lets out.
generated=$(mktemp -d)
trap 'rm -rf "$generated"' EXIT
python3 settings.py "$generated" >/dev/null
cp "$generated/allowlist" proxy/allowlist.generated
cp "$generated/managed-settings.json" managed-settings.generated.json

docker build -q -t legion-proxy:local -f proxy/Dockerfile proxy >/dev/null
docker build -q -t legion-sandbox:local -f Dockerfile \
  --build-arg "EXTRA_APT=${LEGION_SANDBOX_APT:-}" . >/dev/null
if [ -n "${LEGION_SANDBOX_DOCKERFILE:-}" ]; then
  # The extension starts FROM legion-sandbox:local and keeps its user, its settings and its entrypoints.
  docker build -q -t legion-sandbox:local -f "$LEGION_SANDBOX_DOCKERFILE" "$(dirname "$LEGION_SANDBOX_DOCKERFILE")" >/dev/null
fi
rm -f proxy/allowlist.generated managed-settings.generated.json
# A host job that runs `docker image prune -a` deletes an image with no container, and a run container lives only
# during a run. A stopped container keeps the image. Recreate it on each build.
docker rm -f legion-sandbox-keep >/dev/null 2>&1 || true
docker create --name legion-sandbox-keep legion-sandbox:local >/dev/null

# --internal: no route out. The proxy is the only container on both networks.
docker network inspect legion-sandbox >/dev/null 2>&1 || docker network create --internal legion-sandbox >/dev/null
docker rm -f legion-proxy >/dev/null 2>&1 || true
docker run -d --name legion-proxy --restart unless-stopped --memory 128m legion-proxy:local >/dev/null
docker network connect legion-sandbox legion-proxy

# A shared package store keeps each run from downloading every dependency again. It is content-addressed.
docker volume inspect legion-pnpm-store >/dev/null 2>&1 || docker volume create legion-pnpm-store >/dev/null
docker run --rm -u 0 -v legion-pnpm-store:/store alpine:3.20 chown 1001:1001 /store

# One venv for every host process. Each agent lists its own packages in host/requirements.txt.
command -v python3 >/dev/null || { echo "python3 is missing" >&2; exit 1; }
python3 -c "import ensurepip" 2>/dev/null || DEBIAN_FRONTEND=noninteractive apt-get install -y -qq python3-venv
[ -x "$LEGION_VENV/bin/pip" ] || python3 -m venv "$LEGION_VENV"
for requirements in ../../*/host/requirements.txt; do
  [ -f "$requirements" ] && "$LEGION_VENV/bin/pip" install -q -r "$requirements"
done

echo "legion sandbox ready"
