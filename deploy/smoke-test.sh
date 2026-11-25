#!/usr/bin/env bash
# The first run of deploy/README.md, end to end, on this machine's Docker: a setup made by
# `scrooge init --container`, both services of compose.yaml started, the pages signed in to,
# a redacted statement imported and committed, the prices service running. Nothing is left
# behind: the containers and the data folder go at the end, passed or not.
#
#   deploy/smoke-test.sh [image]    default scrooge:test, as `docker build -t scrooge:test .`
#
# DOCKER is the command that runs docker, where it is another: on a Synology NAS,
# DOCKER="sudo /usr/local/bin/docker".
#
# Run from a copy of the repository: it needs deploy/compose.yaml and a fixture of tests/.
# CI runs it on x86-64 and on ARM (.github/workflows/container.yml).
set -euo pipefail

image=${1:-scrooge:test}
port=${SMOKE_PORT:-18765}
repo=$(cd "$(dirname "$0")/.." && pwd)
password='correct horse battery staple' # pragma: allowlist secret
base="http://127.0.0.1:$port"

# Under the repository, not the system's temporary folder: Docker on a Mac (Colima, Docker
# Desktop) shares the home folder with its machine, not /var/folders.
mkdir -p "$repo/output"
work=$(mktemp -d "$repo/output/smoke.XXXXXX")
project="scrooge-smoke-$$"

read -ra docker <<<"${DOCKER:-docker}"
compose() { "${docker[@]}" compose --project-directory "$work" -p "$project" "$@"; }
cleanup() {
    compose down --volumes --timeout 5 >/dev/null 2>&1 || true
    rm -rf "$work"
}
trap cleanup EXIT
fail() {
    echo "smoke test: FAILED: $*" >&2
    compose logs --no-color >&2 || true
    exit 1
}
step() { echo "== $*"; }

cp "$repo/deploy/compose.yaml" "$work/"
mkdir "$work/data"
# Plain permissions, as deploy/README.md asks: on a Synology share a new folder takes the
# share's access lists, which let write only groups the container is not in.
chmod 755 "$work/data"
cat >"$work/.env" <<EOF
SCROOGE_IMAGE=$image
SCROOGE_UID=$(id -u)
SCROOGE_GID=$(id -g)
SCROOGE_PORT=127.0.0.1:$port
EOF

step "scrooge init --container"
# Without a terminal, the password is read from the answers like the rest.
answers=(Test 'TEST PERSON' '' '' localhost "$password" "$password")
printf '%s\n' "${answers[@]}" | compose run --rm -T scrooge scrooge init --container \
    >"$work/init.log" 2>&1 || {
    cat "$work/init.log" >&2
    fail "init"
}
grep -q 'docker compose up -d' "$work/init.log" || fail "init printed no next steps"
grep -q '^  host: 0.0.0.0$' "$work/data/users/test/scrooge.yaml" || fail "no serve in the config"

step "docker compose up"
compose up -d || fail "the services did not start"
# Not `up --wait`: some versions of Compose refuse it for prices, which has no health check.
health=starting
for _ in $(seq 60); do
    health=$("${docker[@]}" inspect -f '{{.State.Health.Status}}' "$(compose ps -q scrooge)")
    [ "$health" = healthy ] && break
    sleep 2
done
[ "$health" = healthy ] || fail "the pages are $health, not healthy"
# Not `compose ps --format`: Compose 2.20 (Synology's) takes no template there.
running=$("${docker[@]}" inspect -f '{{.State.Status}}' "$(compose ps -q prices)")
[ "$running" = running ] || fail "prices is $running, not running"

step "the container"
[ "$(compose exec -T scrooge id -u)" = "$(id -u)" ] || fail "not the user of .env"
if compose exec -T scrooge touch /opt/scrooge/written 2>/dev/null; then
    fail "the image's own files can be written"
fi

step "the pages"
status() { curl -s -o /dev/null -w '%{http_code}' "$@"; }
[ "$(status "$base/")" = 303 ] || fail "/ without signing in is not sent to /login"
jar="$work/cookies"
token=$(curl -fsS -c "$jar" "$base/login" | sed -n 's/.*name="token" value="\([^"]*\)".*/\1/p')
[ -n "$token" ] || fail "no token on /login"
sign_in() {
    status -b "$jar" -c "$jar" --data-urlencode "token=$token" \
        --data-urlencode "password=$1" --data-urlencode "next=/" "$base/login"
}
[ "$(sign_in "$password")" = 303 ] || fail "the password is refused"
# After this one, the pages make whoever tries again wait.
[ "$(sign_in 'not the password at all')" = 401 ] || fail "a wrong password is let in"
[ "$(status -b "$jar" "$base/")" = 200 ] || fail "the overview, signed in"

step "import and commit"
cp "$repo/tests/fixtures/migros/migros.csv" "$work/data/users/test/statements/"
compose exec -T scrooge scrooge import users/test/statements || fail "import"
compose exec -T scrooge scrooge commit || fail "commit"
curl -fsS -b "$jar" "$base/statements/1" | grep -q migros.csv || fail "the statement's page"

echo "smoke test: passed on $("${docker[@]}" info --format '{{.Architecture}}') with $image"
