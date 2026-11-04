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

## The image is built by GitHub

Every tag `v*` pushed to GitHub runs `make check` and, when it passes, builds the image and
pushes it to GitHub's registry, private like the repository: `ghcr.io/<owner>/finanze:<tag>`
and `:latest` (`.github/workflows/image.yml`; `<owner>` in lower case). The NAS only pulls
it: nothing is built there, and no code or git is kept there. The image is built for x86-64,
the NAS's processor.

## On this machine, once

`make nas-update` reaches the NAS by `ssh nas`. In `~/.ssh/config`:

```
Host nas
  HostName nas.example.ts.net
  Port 22
  User <DSM user>
```

Another name works too: `make nas-update NAS=<name>`. `make release` needs `gh`, signed in.

## First time

1. On GitHub, Settings > Developer settings > Personal access tokens > Tokens (classic): a
   token for the NAS with the one scope `read:packages`, nothing else. It can read the
   images and change nothing.

2. In a terminal on the NAS, sign in once (the token is the password; Docker keeps it):

   ```sh
   sudo docker login ghcr.io -u <github-user>
   ```

3. Copy `deploy/compose.yaml` to `/volume1/docker/finanze/` on the NAS (Finder or File
   Station) and adapt the three lines marked `PLACEHOLDER`: the image's owner, the data
   folder, and `user`, the numbers of the NAS user that owns it (`id <user>` in a terminal
   on the NAS prints them). Put the data in `data` next to it (the move itself:
   `docs/nas-migration.md`).

4. On this machine, with `main` pushed, `make release VERSION=v1.0.0`: it tags `main`,
   pushes the tag and waits for the image.

5. Container Manager > Project > Create: name `finanze`, path `/volume1/docker/finanze`,
   use the `compose.yaml` found there, and start. In a terminal on the NAS it is:

   ```sh
   cd /volume1/docker/finanze
   sudo docker compose up -d
   ```

6. Open `http://nas.example.ts.net:8765`: it asks for the password.

A NAS that built the image from an `app/` folder before: steps 1 to 4 (in step 3, only the
`image:` line changes and the `build:` line goes), then `make nas-update`; the folder
`app/` can then be removed.

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

On this machine, once the new version is committed to `main` and pushed:

```sh
make release VERSION=v1.2.3     # tag main, push the tag, wait for GitHub to build the image
make nas-update                 # on the NAS: pull it, restart, drop the image it replaced
```

`nas-update` asks for the DSM password (`sudo`). It is, on the NAS:

```sh
cd /volume1/docker/finanze
sudo docker compose pull
sudo docker compose up -d
sudo docker image prune -f
```

With `image: ...:latest` that is the newest tag; with a tag in `compose.yaml`, the update is
that line changed first. A release whose `make check` fails builds no image, and the NAS
keeps running the one it has. Going back is the same with the older tag in `image:`.

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
`sudo docker load -i finanze-image.tar.gz` on the NAS, and `image: finanze:latest` in
`compose.yaml`.
