#!/usr/bin/env bash
# Take whatever CI last published and restart only what changed.
#
# `up -d` is the whole mechanism: Compose recreates a container when its image
# id or config differs and leaves it alone otherwise, so running this on a
# timer costs a registry check when there is nothing new. That is why there is
# no version bookkeeping here - the thing that knows whether a restart is owed
# is Compose, and asking it is cheaper than tracking it.
#
# Installed as a systemd timer; see docs/deploy-gcp.md.
set -euo pipefail

cd "$(dirname "$(readlink -f "$0")")"

docker compose pull --quiet
docker compose up -d --remove-orphans

# Superseded images are the only thing here that grows without bound. Prune
# keeps anything a running container uses, whatever its age.
docker image prune -af --filter "until=168h" >/dev/null
