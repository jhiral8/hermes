#!/usr/bin/env bash
# Adds a food-create route to NutriTrace's API (POST /api/v1/foods), so the
# Hermes app can put foods into the catalogue and then log them.
#
# NutriTrace keeps running from its normal image. Two patched source files
# (nutritrace-1.3.1/ next to this script) are mounted read-only over the
# originals. Nothing is rebuilt or downloaded. The install refuses to run
# unless the container is NutriTrace 1.3.1 with the exact original files.
#
# Run as root from /opt/hermes-app after `git pull`:
#     sudo bash app/deploy/health-writes/install-nutritrace-food-api.sh --check   # read-only
#     sudo bash app/deploy/health-writes/install-nutritrace-food-api.sh --apply
#
# Needs the health writes from install-health-writes.sh already in place.
# The route only works with PUBLIC_API_WRITE_ENABLED=1 and a token holding
# mcp:write (the Hermes app's hermes-write token). The patch also lets an
# mcp:read token list the food catalogue (mcp:read is documented as covering
# it). Health data never goes to Max or any model.
#
# If NutriTrace's image is ever updated past 1.3.1, remove the two volume
# lines from the override first: they would replace newer files with old ones.

set -euo pipefail

MODE="${1:---check}"
[ "$MODE" = "--check" ] || [ "$MODE" = "--apply" ] || { echo "usage: $0 --check|--apply" >&2; exit 2; }
[ "$(id -u)" -eq 0 ] || { echo "run as root (sudo)" >&2; exit 2; }

HERE="$(cd "$(dirname "$0")" && pwd)"
SRC="$HERE/nutritrace-1.3.1"
STACK=/home/health/health-stack
OVERRIDE="$STACK/docker-compose.override.yml"
BACKUP="$STACK/.override.before-food-api"
PATCHDIR="$STACK/patches/nutritrace"
KEYDIR=/etc/hermes-app/health
APP=nutritrace
URL=http://127.0.0.1:3001/api/v1
HUID=$(id -u health)
SOCK="/run/user/$HUID/docker.sock"

# Originals in NutriTrace 1.3.1, and the patched files shipped here.
ORIG_CREATE=13a3c169860c3c63cc25f69c02972af0fd23ba7dad01257460e69a3059c048e4
ORIG_FOODS=491e01c1e33dcb6e6f00c97383e56221c9ed38d0fbffc1ce43bbca76fee191e6
NEW_CREATE=64c09feec982baef5063df870b9405426698ae0ea2998a034d21ec9ce1a9b172
NEW_FOODS=6ea84c922a41375b0c286e3d7ba80c2fda32a4ce6647321c19bc5449c28f90ed

CHANGED=0
fail() {
  echo "STOP: $*" >&2
  if [ "$CHANGED" = 1 ]; then
    echo "Changes HAVE started. To undo: sudo cp $BACKUP $OVERRIDE, then restart $APP" >&2
    echo "(docker compose up -d --no-deps --pull never $APP in $STACK, as health)." >&2
  else
    echo "Nothing was changed." >&2
  fi
  exit 1
}

hdocker() {
  runuser -u health -- env XDG_RUNTIME_DIR="/run/user/$HUID" DOCKER_HOST="unix://$SOCK" docker "$@"
}
hcompose() {
  runuser -u health -- bash -c "cd '$STACK' && env XDG_RUNTIME_DIR='/run/user/$HUID' DOCKER_HOST='unix://$SOCK' docker compose $*"
}
sha() { sha256sum "$1" | cut -d' ' -f1; }

# ---- Checks (no changes) -------------------------------------------------

[ "$(sha "$SRC/create-food.js")" = "$NEW_CREATE" ] || fail "patched create-food.js in the repo is not the reviewed version"
[ "$(sha "$SRC/foods.js")" = "$NEW_FOODS" ] || fail "patched foods.js in the repo is not the reviewed version"
[ -S "$SOCK" ] || fail "rootless Docker socket not found at $SOCK"
[ -f "$OVERRIDE" ] || fail "$OVERRIDE is missing; run install-health-writes.sh first"
grep -q 'PUBLIC_API_WRITE_ENABLED=1' "$OVERRIDE" || fail "health writes are not on in $OVERRIDE"
! grep -q 'patches/nutritrace' "$OVERRIDE" || fail "the food API patch is already in $OVERRIDE"
[ ! -e "$PATCHDIR" ] || fail "$PATCHDIR already exists; read it first, then decide"
[ -r "$KEYDIR/$APP-write.key" ] || fail "$KEYDIR/$APP-write.key is missing"
[ -r "$KEYDIR/$APP.key" ] || fail "$KEYDIR/$APP.key (the read token) is missing"

st=$(hdocker inspect -f '{{.State.Health.Status}}' "$APP" 2>/dev/null || true)
[ "$st" = "healthy" ] || fail "$APP is not healthy (status: ${st:-missing})"
ver=$(hdocker exec "$APP" node -p "require('/app/package.json').version" 2>/dev/null || true)
[ "$ver" = "1.3.1" ] || fail "$APP is version '${ver:-unknown}', the patch is for 1.3.1"
c=$(hdocker exec "$APP" sha256sum /app/lib/mcp/tools/create-food.js 2>/dev/null | cut -d' ' -f1 || true)
f=$(hdocker exec "$APP" sha256sum /app/routes/api/v1/foods.js 2>/dev/null | cut -d' ' -f1 || true)
[ "$c" = "$ORIG_CREATE" ] || fail "$APP's create-food.js is not the 1.3.1 original"
[ "$f" = "$ORIG_FOODS" ] || fail "$APP's api/v1/foods.js is not the 1.3.1 original"
grep -qE '^  nutritrace:$' "$OVERRIDE" || fail "can't find the nutritrace entry in $OVERRIDE"

echo "ok: $APP 1.3.1 healthy, original files match, writes on, tokens present"
echo "All checks passed."
if [ "$MODE" = "--check" ]; then
  echo "Check only. Nothing changed. Run with --apply to make the change."
  exit 0
fi

# ---- Changes -------------------------------------------------------------

cp -p "$OVERRIDE" "$BACKUP"
CHANGED=1

install -d -o health -g health -m 755 "$STACK/patches" "$PATCHDIR"
install -o health -g health -m 644 "$SRC/create-food.js" "$PATCHDIR/create-food.js"
install -o health -g health -m 644 "$SRC/foods.js" "$PATCHDIR/foods.js"

tmp=$(mktemp "$STACK/.override.XXXXXX")
awk '{ print } $0 == "  nutritrace:" {
  print "    volumes:"
  print "      - ./patches/nutritrace/create-food.js:/app/lib/mcp/tools/create-food.js:ro"
  print "      - ./patches/nutritrace/foods.js:/app/routes/api/v1/foods.js:ro"
}' "$OVERRIDE" > "$tmp"
chown health:health "$tmp"
chmod 600 "$tmp"
mv "$tmp" "$OVERRIDE"

merged=$(hcompose "config" 2>&1) || { cp -p "$BACKUP" "$OVERRIDE"; CHANGED=0; fail "compose config does not validate; override restored"; }
echo "$merged" | grep -q '/app/routes/api/v1/foods.js' || { cp -p "$BACKUP" "$OVERRIDE"; CHANGED=0; fail "merged config lacks the mounts; override restored"; }

hcompose "up -d --no-deps --pull never $APP" >/dev/null
st=""
for _ in $(seq 1 75); do
  st=$(hdocker inspect -f '{{.State.Health.Status}}' "$APP" 2>/dev/null || true)
  [ "$st" = "healthy" ] && break
  sleep 2
done
[ "$st" = "healthy" ] || fail "$APP did not become healthy within 150s (status: ${st:-missing})"

c=$(hdocker exec "$APP" sha256sum /app/lib/mcp/tools/create-food.js | cut -d' ' -f1)
f=$(hdocker exec "$APP" sha256sum /app/routes/api/v1/foods.js | cut -d' ' -f1)
[ "$c" = "$NEW_CREATE" ] && [ "$f" = "$NEW_FOODS" ] || fail "$APP is not running the patched files"
echo "restarted: $APP healthy with the patched files"

# ---- Proof without writing anything --------------------------------------
# An empty name is refused by the new route with 400, so this proves the
# route exists and the write token reaches it, and creates no food.
wtok=$(cat "$KEYDIR/$APP-write.key")
body=$(curl -s -w ' %{http_code}' -X POST -H "Authorization: Bearer $wtok" -H 'Content-Type: application/json' \
  -d '{"name":""}' "$URL/foods" || true)
case "$body" in
  *'"code":"invalid"'*' 400') echo "verified: POST /api/v1/foods is live (empty name refused, nothing written)" ;;
  *) fail "POST /api/v1/foods did not answer as expected: $body" ;;
esac
rtok=$(cat "$KEYDIR/$APP.key")
code=$(curl -s -o /dev/null -w '%{http_code}' -H "Authorization: Bearer $rtok" "$URL/foods?limit=1" || true)
[ "$code" = "200" ] || fail "the read token can't list foods (HTTP $code)"
echo "verified: the read token can list the food catalogue"

echo "Done. NutriTrace has POST /api/v1/foods."
echo "Undo: sudo cp $BACKUP $OVERRIDE, then restart $APP."
