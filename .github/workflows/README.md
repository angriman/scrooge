# GitHub Actions

| Workflow | Runs on | What it does |
| --- | --- | --- |
| [`ci.yml`](ci.yml) (CI) | every pull request, every push to `main` | `lint and types`: ruff and `mypy --strict` (Linux, Python 3.11). `test`: pytest on Linux, Python 3.11 and 3.14; the 3.11 run also enforces the coverage floors. `frontend`: `make web-check web`. `check`: passes when all of them passed; the only check `main` requires. |
| [`container.yml`](container.yml) (container) | pull requests that touch what goes in the image; every release | Builds the image and runs `deploy/smoke-test.sh` on x86-64 and ARM, natively. |
| [`coverage-badge.yml`](coverage-badge.yml) (coverage badge) | after CI passes on a push to `main` | Writes the coverage total of that run to the branch `badges`, read by the README's badge. |
| [`release.yml`](release.yml) (release) | a tag `v*` (`make release VERSION=v1.2.3`), or by hand | Runs CI and the container smoke test, then builds the image for both processors and pushes it to `ghcr.io/<owner>/scrooge`. |

`make check` locally is the same gate as the `lint and types` job plus the coverage run of
`test`; `make web-check` is the `frontend` job.
