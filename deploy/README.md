# The web app on the NAS

A container that runs `finanze serve` over a copy of the database, to read the pages from
other devices. The machine the statements are imported on stays the one place the data
changes; the NAS only shows what is copied to it.

Meant for a Synology NAS with an x86-64 processor and Container Manager. Paths and names
below are placeholders: `/volume1/docker/finanze` on the NAS, `nas.example.ts.net` for what
the devices call it.

## What is where

| On the NAS, in `/volume1/docker/finanze/` | What |
|---|---|
| `compose.yaml` | copied once from `deploy/compose.yaml` and adapted |
| `app/` | what the image is built from: `Dockerfile`, `pyproject.toml`, `uv.lock`, `src/`; replaced on every update |
| `data/` | `finanze.yaml` and `finanze.db`, mounted read-only as `/data` |

`finanze.yaml` on the NAS needs only this (`database` in it is not read: the container
always opens `/data/finanze.db`):

```yaml
serve:
  host: 0.0.0.0                # inside the container; compose.yaml says what the NAS publishes
  names: [nas.example.ts.net]  # every name or address typed in a browser to reach it
  password: scrypt$...         # the line `finanze password` prints
```

The app refuses to start beyond its own machine without the password, and answers only
to the names listed (an address used instead of a name is listed too).

## The image is built on the NAS

The source is copied to the NAS and Container Manager builds it there. The other way,
building on this machine and carrying the image over as a file, needs a container engine
here and a build for another processor than this machine's; the NAS has the engine and is
the processor the image is for. The price: the NAS needs the internet while it builds
(the Python image, `uv`, the packages `uv.lock` names), and never afterwards.

## First time

1. On this machine, from the project folder:

   ```sh
   make nas-app
   ```

   `output/nas/app/` now holds the committed code and `uv.lock`, and nothing else: no
   config, no database, no statements, no personal rules.

2. Copy to `/volume1/docker/finanze/` on the NAS (Finder or File Station): the folder
   `output/nas/app` as `app`, and `deploy/compose.yaml`. Create `data` next to them, with
   `finanze.yaml` and `finanze.db` in it.

3. In `compose.yaml` on the NAS, adapt the two lines marked `PLACEHOLDER`: the data
   folder, and `user`, the numbers of a NAS user that may read it (`id <user>` in a
   terminal on the NAS prints them).

4. Container Manager > Project > Create: name `finanze`, path `/volume1/docker/finanze`,
   use the `compose.yaml` found there, build and start. In a terminal on the NAS it is:

   ```sh
   cd /volume1/docker/finanze
   sudo docker compose up -d --build
   ```

5. Open `http://nas.example.ts.net:8765`: it asks for the password.

## A new database

Put the new `finanze.db` in the data folder in place of the old one. Every page opens the
database anew: nothing is restarted. The image must know the database's schema: after a
change that adds a migration, update the app first.

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

Everyone signs in again: sessions live in the app's memory.

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
folder its user cannot read, a database newer or older than the app.

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
