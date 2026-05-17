#!/bin/sh
set -eu

: "${DOCKFLARE_BASIC_AUTH_USER:?DOCKFLARE_BASIC_AUTH_USER must be set}"
: "${DOCKFLARE_BASIC_AUTH_PASSWORD:?DOCKFLARE_BASIC_AUTH_PASSWORD must be set}"

printf "%s:%s\n" \
  "$DOCKFLARE_BASIC_AUTH_USER" \
  "$(openssl passwd -apr1 "$DOCKFLARE_BASIC_AUTH_PASSWORD")" \
  > /etc/nginx/.htpasswd

exec /docker-entrypoint.sh "$@"
