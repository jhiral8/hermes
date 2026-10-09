#!/usr/bin/env bash
# Turns on write access in NutriTrace, LiftTrace and CookTrace for the Hermes
# app, and creates each app's write token on the server (no key step for Craig).
#
# Run as root from /opt/hermes-app after `git pull`:
#     sudo bash app/deploy/health-writes/install-health-writes.sh --check   # read-only
#     sudo bash app/deploy/health-writes/install-health-writes.sh --apply   # makes the change
#
# --check changes nothing. --apply runs every check first and stops before
# changing anything if one fails. Nothing here touches the main compose file or
# any .env file: the switch lives in docker-compose.override.yml, which Docker
# Compose loads automatically from the same folder.
#
# Health data never goes to Max or any model. The write tokens are stored
# root-owned in /etc/hermes-app/health and are read by the Hermes app only.

set -euo pipefail

MODE="${1:---check}"
[ "$MODE" = "--check" ] || [ "$MODE" = "--apply" ] || { echo "usage: $0 --check|--apply" >&2; exit 2; }
[ "$(id -u)" -eq 0 ] || { echo "run as root (sudo)" >&2; exit 2; }

STACK=/home/health/health-stack
OVERRIDE="$STACK/docker-compose.override.yml"
KEYDIR=/etc/hermes-app/health
TOKEN_NAME=hermes-write
APPS=(nutritrace lifttrace cooktrace)
declare -A PORT=([nutritrace]=3001 [lifttrace]=3002 [cooktrace]=3003)
HUID=$(id -u health)
SOCK="/run/user/$HUID/docker.sock"

# CHANGED flips to 1 the moment the first change is made, so the failure message
# below is accurate about what is on the server.
CHANGED=0
fail() {
  echo "STOP: $*" >&2
  if [ "$CHANGED" = 1 ]; then
    echo "Changes HAVE started on the server. Current state:" >&2
    echo "  override: $([ -e "$OVERRIDE" ] && echo "present ($OVERRIDE)" || echo "removed")" >&2
    echo "  tokens:   $(ls "$KEYDIR"/*-write.key 2>/dev/null | tr '\n' ' ' || true)" >&2
    echo "Undo: sudo rm $OVERRIDE, then restart each app with docker compose up -d --no-deps --pull never <app>." >&2
    echo "Remove a token by deleting its row in the app's api_tokens table if needed." >&2
  else
    echo "Nothing was changed." >&2
  fi
  exit 1
}

# Run docker as the health user (rootless Docker in the stack folder).
hdocker() {
  runuser -u health -- env XDG_RUNTIME_DIR="/run/user/$HUID" DOCKER_HOST="unix://$SOCK" \
    docker "$@"
}
hcompose() {
  runuser -u health -- bash -c "cd '$STACK' && env XDG_RUNTIME_DIR='/run/user/$HUID' DOCKER_HOST='unix://$SOCK' docker compose $*"
}

# ---- Checks (no changes) -------------------------------------------------

[ -f "$STACK/docker-compose.yml" ] || fail "compose file not found at $STACK/docker-compose.yml"
[ -S "$SOCK" ] || fail "rootless Docker socket not found at $SOCK (is health's user service running?)"
[ -d "$KEYDIR" ] || fail "$KEYDIR is missing"
[ ! -e "$OVERRIDE" ] || fail "$OVERRIDE already exists; read it first, then decide"

for app in "${APPS[@]}"; do
  st=$(hdocker inspect -f '{{.State.Health.Status}}' "$app" 2>/dev/null || true)
  [ "$st" = "healthy" ] || fail "$app container is not healthy (status: ${st:-missing})"

  hdocker exec "$app" test -f /app/lib/api-tokens.js || fail "$app has no /app/lib/api-tokens.js"
  hdocker exec "$app" test -f /app/db.js || fail "$app has no /app/db.js"

  # Not already switched on, and no write token from an earlier run.
  if hdocker exec "$app" printenv PUBLIC_API_WRITE_ENABLED 2>/dev/null | grep -qx 1; then
    fail "$app already has PUBLIC_API_WRITE_ENABLED=1"
  fi
  existing=$(hdocker exec "$app" node --input-type=module -e \
    "import db from '/app/db.js'; const r = db.prepare('SELECT COUNT(*) AS n FROM api_tokens WHERE name = ?').get('$TOKEN_NAME'); process.stdout.write(String(r.n));" 2>/dev/null || echo "?")
  [ "$existing" = "0" ] || fail "$app already has $existing token(s) named $TOKEN_NAME (got '$existing')"

  users=$(hdocker exec "$app" node --input-type=module -e \
    "import db from '/app/db.js'; const r = db.prepare('SELECT COUNT(*) AS n FROM users').get(); process.stdout.write(String(r.n));" 2>/dev/null || echo "0")
  [ "$users" -ge 1 ] || fail "$app has no user row to own the token (got '$users')"

  code=$(curl -s -o /dev/null -w '%{http_code}' "http://127.0.0.1:${PORT[$app]}/api/v1/me" || true)
  echo "ok: $app healthy, token code present, $users user row(s), /api/v1/me -> HTTP $code"
done

echo "All checks passed."
if [ "$MODE" = "--check" ]; then
  echo "Check only. Nothing changed. Run with --apply to make the change."
  exit 0
fi

# ---- Changes -------------------------------------------------------------

# 1. Write the override: PUBLIC_API_WRITE_ENABLED=1 for these three apps only.
umask 022
tmp=$(mktemp "$STACK/.override.XXXXXX")
cat > "$tmp" <<'YAML'
# Added by the Hermes app install: write routes on, nothing else changed.
services:
  nutritrace:
    environment:
      - PUBLIC_API_WRITE_ENABLED=1
  lifttrace:
    environment:
      - PUBLIC_API_WRITE_ENABLED=1
  cooktrace:
    environment:
      - PUBLIC_API_WRITE_ENABLED=1
YAML
chown health:health "$tmp"
chmod 600 "$tmp"
mv "$tmp" "$OVERRIDE"
CHANGED=1

# Make sure the merged config really has the switch before restarting anything.
merged=$(hcompose "config" 2>&1) || { rm -f "$OVERRIDE"; CHANGED=0; fail "compose config does not validate with the override; removed it"; }
for app in "${APPS[@]}"; do
  echo "$merged" | awk -v s="$app:" '$0 ~ "^  "s {f=1; next} /^[^ ]/ || /^  [^ ]/ {f=0} f' \
    | grep -q 'PUBLIC_API_WRITE_ENABLED' || { rm -f "$OVERRIDE"; CHANGED=0; fail "merged config for $app lacks the switch; removed override"; }
done

# 2. Create each app's write token inside its own container (no sign-in, no key
#    from Craig), and store it root-owned for the Hermes app only.
for app in "${APPS[@]}"; do
  keyfile="$KEYDIR/$app-write.key"
  hdocker exec "$app" node --input-type=module -e "
    import { createToken } from '/app/lib/api-tokens.js';
    import db from '/app/db.js';
    const u = db.prepare('SELECT id FROM users ORDER BY id LIMIT 1').get();
    const { raw } = createToken({ userId: u.id, name: '$TOKEN_NAME', scopes: ['mcp:read', 'mcp:write'] });
    process.stdout.write(raw);" > "$keyfile.new"
  [ -s "$keyfile.new" ] || { rm -f "$keyfile.new"; fail "$app token creation returned nothing"; }
  chown root:hermes-app "$keyfile.new"
  chmod 640 "$keyfile.new"
  mv "$keyfile.new" "$keyfile"
  echo "token stored: $keyfile (value not shown)"
done

# 3. Restart one app at a time. The healthcheck runs every 60s, so wait longer
#    than one interval (up to 150s) before deciding it is unhealthy.
for app in "${APPS[@]}"; do
  hcompose "up -d --no-deps --pull never $app" >/dev/null
  st=""
  for _ in $(seq 1 75); do
    st=$(hdocker inspect -f '{{.State.Health.Status}}' "$app" 2>/dev/null || true)
    [ "$st" = "healthy" ] && break
    sleep 2
  done
  [ "$st" = "healthy" ] || fail "$app did not become healthy within 150s (status: ${st:-missing})"
  hdocker exec "$app" printenv PUBLIC_API_WRITE_ENABLED | grep -qx 1 || fail "$app is up but PUBLIC_API_WRITE_ENABLED is not 1"
  echo "restarted: $app healthy with writes on"
done

# 4. Read-only proof. The token must be accepted on a read route, and its
#    stored scopes must include mcp:write. LiftTrace has no /api/v1/me, so the
#    scope check reads the api_tokens table in each container instead.
for app in "${APPS[@]}"; do
  tok=$(cat "$KEYDIR/$app-write.key")
  if [ "$app" = "lifttrace" ]; then read_path=/api/v1/programs; else read_path=/api/v1/me; fi
  code=$(curl -s -o /dev/null -w '%{http_code}' -H "Authorization: Bearer $tok" \
    "http://127.0.0.1:${PORT[$app]}$read_path" || true)
  [ "$code" = "200" ] || fail "$app: token refused on $read_path (HTTP $code)"
  scopes=$(hdocker exec "$app" node --input-type=module -e \
    "import db from '/app/db.js'; const r = db.prepare('SELECT scopes FROM api_tokens WHERE name = ?').all('$TOKEN_NAME'); process.stdout.write(r.map(x => x.scopes).join(' '));" 2>/dev/null || true)
  echo "$scopes" | grep -q 'mcp:write' || fail "$app: stored token has no mcp:write scope"
  echo "verified: $app token accepted on $read_path, mcp:write present"
done

echo "Done. Health writes are on for NutriTrace, LiftTrace and CookTrace."
echo "Rollback: sudo rm $OVERRIDE, then restart each app with docker compose up -d --no-deps --pull never <app>."
