# The app on the NAS

A container that runs `finanze serve` over the one database: statements are imported and
committed from the pages (or with `docker exec`, below), prices are fetched there, and the
other machines only look at it. The Mac is for development, with a test database
(`make dev-db`).

Meant for a Synology NAS with an x86-64 processor and Container Manager. Paths and names
below are placeholders: `/volume1/docker/finanze` on the NAS, `nas.example.ts.net` for what
the devices call it.

## What is where

| On the NAS, in `/volume1/docker/finanze/` | What |
|---|---|
| `compose.yaml` | copied once from `deploy/compose.yaml` and adapted |
| `app/` | what the image is built from: `Dockerfile`, `pyproject.toml`, `uv.lock`, `src/`; replaced on every update |
| `data/` | everything personal, mounted read-write as `/data` (below) |

| In `data/` | What |
|---|---|
| `finanze.yaml` | the config (below) |
| `finanze.db` | the database |
| `backups/` | a copy of the database before every commit and before a new schema |
| `originals/` | every statement imported, as it came |
| `rules.yaml`, `rules.<owner>.yaml` | the category rules, both people's and each one's |
| `categories.yaml` | the categories added to the built-in ones |
| `instruments.yaml` | the asset tab of what is held, bill maturities, price symbols |

The image holds only the code. `examples/` in the project has a starting point for the three
YAML files that are not the config.

`finanze.yaml` on the NAS:

```yaml
database: /data/finanze.db
owners:                        # as on the Mac
  ...
account_labels:                # as on the Mac, if any
  ...
serve:
  host: 0.0.0.0                # inside the container; compose.yaml says what the NAS publishes
  names: [nas.example.ts.net]  # every name or address typed in a browser to reach it
  password: scrypt$...         # the line `finanze password` prints
```

Not `read_only` and not `publish`: this is the database itself, not a copy. The app refuses
to start beyond its own machine without the password, and answers only to the names listed
(an address used instead of a name is listed too). `rules` and `instruments` need not be
named: the container runs in `/data`, where they are.

The data folder must belong to the user `compose.yaml` names: the app writes there. In a
terminal on the NAS: `sudo chown -R 1026:100 /volume1/docker/finanze/data` (the numbers of
that user).

## The image is built on the NAS

The source is copied to the NAS and Container Manager builds it there. The other way,
building on this machine and carrying the image over as a file, needs a container engine
here and a build for another processor than this machine's; the NAS has the engine and is
the processor the image is for. The price: the NAS needs the internet while it builds
(the Python image, Tesseract, `uv`, the packages `uv.lock` names).

## First time

1. On this machine, from the project folder:

   ```sh
   make nas-app
   ```

   `output/nas/app/` now holds the committed code and `uv.lock`, and nothing else: no
   config, no database, no statements, no rules.

2. Copy to `/volume1/docker/finanze/` on the NAS (Finder or File Station): the folder
   `output/nas/app` as `app`, and `deploy/compose.yaml`. Put the data in `data` next to
   them (the move itself: `docs/nas-migration.md`).

3. In `compose.yaml` on the NAS, adapt the two lines marked `PLACEHOLDER`: the data
   folder, and `user`, the numbers of the NAS user that owns it (`id <user>` in a terminal
   on the NAS prints them).

4. Container Manager > Project > Create: name `finanze`, path `/volume1/docker/finanze`,
   use the `compose.yaml` found there, build and start. In a terminal on the NAS it is:

   ```sh
   cd /volume1/docker/finanze
   sudo docker compose up -d --build
   ```

5. Open `http://nas.example.ts.net:8765`: it asks for the password.

## Commands

What the pages do not do runs inside the container, in `/data`, as the container's user:

```sh
sudo docker exec finanze finanze statements --owner Name
sudo docker exec finanze finanze balance --owner Name "Account" 2026-10-31 1234.50 CHF
sudo docker exec finanze finanze uncommit --owner Name 42
sudo docker exec finanze finanze query --from 2026-09 --text rent
sudo docker exec finanze finanze keep originals-to-add/
sudo docker exec -it finanze finanze import --owner Name --review statements/
```

A path given to a command is a path inside the container: under `/data`.

## Prices, every day

`finanze prices` is the one command that goes online (D10). DSM > Control Panel > Task
Scheduler > Create > Scheduled Task > User-defined script, user `root`, daily after the
markets close (say 23:30), script:

```sh
docker exec finanze finanze prices
```

It asks only for the days not stored yet, so a missed day is caught up the next time.

## A new version of the app

```sh
make nas-app
```

Replace `/volume1/docker/finanze/app` with the new `output/nas/app`, then Container
Manager > Project > finanze > Action > Build, or:

```sh
cd /volume1/docker/finanze
sudo docker compose up -d --build
sudo docker image prune -f      # drop the image it replaced
```

A version with a new schema brings the database up to date as it starts, after copying it
to `data/backups/`; the log says so. When that fails the container does not start, and the
log says why and where the copy is. Everyone signs in again: sessions live in the app's
memory.

## Looking at it

Container Manager > Container > finanze shows the state (`healthy` once `/login`
answers) and, under Log, what the app printed. In a terminal:

```sh
cd /volume1/docker/finanze
sudo docker compose ps            # running, healthy
sudo docker compose logs -f       # what it printed; Ctrl-C to leave
sudo docker compose restart       # after changing finanze.yaml
sudo docker compose down          # stop and remove the container; the data stays
```

A container that keeps restarting says why in its log: a config `serve` refuses, a data
folder its user cannot write, a migration that failed.

## Building on this machine instead

With a container engine here, the same `Dockerfile` builds the image for the NAS, from
the project folder:

```sh
docker build --platform linux/amd64 -t finanze:latest .
docker save finanze:latest | gzip > output/finanze-image.tar.gz
```

Then Container Manager > Image > Action > Import > Add From File, or
`sudo docker load -i finanze-image.tar.gz` on the NAS. Without `app/` there, take the
`build:` line out of `compose.yaml`.
