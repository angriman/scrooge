# The app in Docker

A container that runs `scrooge serve` over the one database, and a second one that fetches
prices every day. Statements are imported and committed from the pages (or with
`docker compose exec`, below), and every device with a browser looks at them.

Any machine with Docker and Docker Compose runs it: a Linux server, a NAS (Synology,
QNAP, Unraid), a Raspberry Pi 4 or 5 with a 64-bit system, a Mac or a Windows PC with Docker
Desktop. The image is built for x86-64 and 64-bit ARM, and is public:
`ghcr.io/angriman/scrooge`.

## First time

In a folder of its own, say `~/scrooge`:

```sh
mkdir -p ~/scrooge/data && cd ~/scrooge
curl -fsSLO https://raw.githubusercontent.com/angriman/scrooge/main/deploy/compose.yaml
curl -fsSL -o .env https://raw.githubusercontent.com/angriman/scrooge/main/deploy/.env.example
```

1. **`.env`**: `SCROOGE_UID` and `SCROOGE_GID` are the user and group that own `data/`
   (`id -u` and `id -g` print them). The rest can stay as it is: the newest release, `data/`
   next to `compose.yaml`, port 8765. Make `data/` yourself, as that user: a folder Docker
   makes belongs to root, and the app could not write there.

2. **The setup**, once. It asks who you are, how your name is printed on statements, what
   is typed in a browser to reach the pages (`localhost`, the machine's name, its address,
   all of them) and a password for them:

   ```sh
   docker compose run --rm scrooge scrooge init --container
   ```

   It writes `data/users/<name>/`: the config, the rules, the categories, the instruments,
   `statements/` and the database, empty. Only `init` makes a database: the pages and every
   command refuse to start without one.

3. **Start**:

   ```sh
   docker compose up -d
   ```

4. Open `http://localhost:8765`, or the machine's name with `:8765` from another device: it
   asks for the password. Statements go in on the Import page.

## What is where

| In `data/` | What |
|---|---|
| `users/<name>/scrooge.yaml` | the config |
| `users/<name>/scrooge.db` | the database |
| `users/<name>/backups/` | a copy of the database before every commit and before a new schema |
| `users/<name>/originals/` | every statement imported, as it came |
| `users/<name>/rules.yaml`, `rules.<owner>.yaml` | the category rules, both people's and each one's |
| `users/<name>/categories.yaml` | the categories added to the built-in ones |
| `users/<name>/instruments.yaml` | the asset tab of what is held, bill maturities, price symbols |

The image holds only the code. Back up `data/`, and that is everything.

A config in `data/scrooge.yaml` itself, with its paths under `/data`, is used instead of the
setup in `users/` (a setup made before `scrooge init` existed). If its database is not there
yet, `docker compose run --rm scrooge scrooge init --db /data/scrooge.db` makes an empty one
(the path the config names). The container reads the one setup there is; with several,
`SCROOGE_USER=<name>` in the `environment` of both services chooses.

The part of the config that `init --container` writes about the pages:

```yaml
serve:
  host: 0.0.0.0                       # every address of the container; compose.yaml says
                                      # which port of the machine leads there
  names: [localhost, nas.example.ts.net]  # every name or address typed in a browser
  password: scrypt$...                # the line `scrooge password` prints
```

The app refuses to start beyond its own container without the password, and answers only to
the names listed, besides `localhost` and `127.0.0.1`. A name added later goes in that list,
then `docker compose restart scrooge`.

## Commands

What the pages do not do runs inside the container, in `/data`, from the folder of
`compose.yaml`:

```sh
docker compose exec scrooge scrooge statements --owner Name
docker compose exec scrooge scrooge balance --owner Name "Account" 2026-10-31 1234.50 CHF
docker compose exec scrooge scrooge position --owner Name "Broker" 2026-10-31 ACME 120 USD
docker compose exec scrooge scrooge close --owner Name "Account" 2026-10-31   # --reopen
docker compose exec scrooge scrooge uncommit --owner Name 42
docker compose exec scrooge scrooge query --from 2026-09 --text rent
docker compose exec scrooge scrooge password
docker compose exec scrooge scrooge keep originals-to-add/
docker compose exec scrooge scrooge import --owner Name --review users/name/statements/
docker compose exec scrooge scrooge migrate-files          # then with --write
docker compose exec scrooge scrooge doctor                 # what is wrong; changes nothing
```

A path given to a command is a path inside the container: under `/data`.

## Prices, every day

The service `prices` runs `scrooge prices`, the one command that goes online (D10), as it
starts and then every 24 hours. It asks only for the days not stored yet, so a missed day is
caught up the next time. `docker compose logs prices` shows what it fetched.

To run it from the machine's own scheduler instead, remove that service from `compose.yaml`
and schedule `docker compose exec -T scrooge scrooge prices` in the folder of `compose.yaml`
(cron, a systemd timer, the NAS's task scheduler).

## A new version

```sh
docker compose pull
docker compose up -d
docker image prune -f      # the image it replaced
```

With `:latest` in `SCROOGE_IMAGE` that is the newest release; with a tag, the update is that
tag changed first. Going back is the same with the older tag.

A version with a new schema brings the database up to date as it starts, after copying it to
`backups/`; the log says so. When that fails the container does not start, and the log says
why and where the copy is. Everyone signs in again: sessions live in the app's memory.

Before an update, `scrooge doctor` (see Commands) says whether the database's schema is older
than the code's, which the new version would migrate, and whatever else is wrong: the rules,
the originals, the prices. It changes nothing.

## Looking at it

```sh
docker compose ps                 # running; the pages healthy once /login answers
docker compose logs -f            # what both printed; Ctrl-C to leave
docker compose restart scrooge    # after changing the config
docker compose down               # stop and remove the containers; the data stays
```

A container that keeps restarting says why in its log: no setup yet (step 2), a config
`serve` refuses, a data folder its user cannot write, a migration that failed.

## From other devices

- **At home**: the machine's name or address, listed in `names`.
- **Away**: a VPN such as Tailscale or WireGuard, the tailnet's name listed in `names`.
  `SCROOGE_PORT=100.x.y.z:8765` answers on the tailnet's address alone.
- **Behind a reverse proxy** (Caddy, Traefik, nginx) for HTTPS: `SCROOGE_PORT=127.0.0.1:8765`
  when the proxy runs on the same machine, the proxy's public name in `names`, and the proxy
  passing the `Host` it was asked for.

The pages hold a household's finances behind one password: keep them off the open internet.

## Platform notes

### Synology NAS

- The folder: `/volume1/docker/scrooge`, with `compose.yaml`, `.env` and `data/` in it
  (File Station, or a terminal on the NAS).
- `SCROOGE_UID`/`SCROOGE_GID`: `id <user>` in a terminal on the NAS, for the DSM user that
  owns `data/`; `sudo chown -R <uid>:<gid> /volume1/docker/scrooge/data` if needed.
- `chmod 755 /volume1/docker/scrooge/data`, as the user that owns it, before the first start.
  A folder made on a share takes the share's access lists, which let write the
  administrators group: the container runs as `SCROOGE_UID:SCROOGE_GID` alone, in no other
  group, and could only read. `chmod` gives the folder plain permissions instead, and the
  folders made in it follow.
- Container Manager > Project > Create: name `scrooge`, path `/volume1/docker/scrooge`, use
  the `compose.yaml` found there. Or in a terminal: `sudo docker compose up -d` there.
- Commands need `sudo`, and in a scheduled task or over ssh DSM's docker is
  `/usr/local/bin/docker`: no login shell puts it on the `PATH`.

### Raspberry Pi

A 64-bit Raspberry Pi OS (or any arm64 system) on a Pi 4 or 5; a 32-bit system cannot run the
image. Docker's convenience script installs Docker with Compose.

### Mac and Windows

Docker Desktop. `SCROOGE_UID`/`SCROOGE_GID` as `id -u` and `id -g` print on a Mac; on
Windows the defaults work.

## Building the image yourself

From a clone of the repository, for the machine's own processor:

```sh
docker build -t scrooge:local .
```

and `SCROOGE_IMAGE=scrooge:local` in `.env`. For another machine:
`docker build --platform linux/arm64 -t scrooge:local .` (or `linux/amd64`), then
`docker save scrooge:local | gzip > scrooge.tar.gz` and `docker load -i scrooge.tar.gz` there.

## For the maintainer: releases

Every tag `v*` pushed to GitHub runs `make check` and, when it passes, builds the image for
both processors and pushes `ghcr.io/<owner>/scrooge:<tag>` and `:latest`
(`.github/workflows/image.yml`). Nothing is built where the app runs.

```sh
make release VERSION=v1.2.3     # tag main, push the tag, wait for GitHub to build the image
make deploy-update              # where the app runs: pull it, restart, drop the image replaced
```

`deploy-update` runs the three commands of "A new version" over ssh. Where is set in
`Makefile.local` (git-ignored), for example for a Synology NAS:

```make
DEPLOY_HOST = nas                     # a Host of ~/.ssh/config
DEPLOY_DIR = /volume1/docker/scrooge  # the folder of compose.yaml
DEPLOY_DOCKER = /usr/local/bin/docker # by its path: ssh gives no login shell
DEPLOY_SUDO = sudo                    # asks the DSM password
```

A release whose `make check` fails builds no image, and the app keeps running the one it has.
