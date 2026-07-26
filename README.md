![alt text](https://github.com/Klozeitung/capy_barca/blob/main/frontend/public/CapyBarca.png "CapyBarca Logo")

# CapyBarca

CapyBarca is a self-hosted personal knowledge management application. It runs entirely on your own infrastructure, requires no cloud account, and keeps your data under your control.

> Licensed under [MIT](LICENSE). Copyright (c) 2026 Klozeitung.

---

## Features

### The Basics

- **Pages and blocks** hierarchical workspace with rich-text pages, headings, lists, code blocks, and file attachments
- **Databases** structured tables with custom property types (text, select, date, relation, formula, rollup, and more), filters, sorting, and multiple views per database
- **Backup and restore** portable `.capy` backup files covering database, uploads, and configuration; full restore via a single script
- **Multi-user** admin and member roles, optional self-registration
- **Supported Languages**: English, German
- **Exports**: Export your data (the current view) to pdf or csv with ease.

### Advanced features

- **Automations** rule-based triggers that act on database entries
- **Comments** inline comments on pages and database entries
- **Collaborative editing** real-time document collaboration via integrated Collabora Online (WOPI)

### CapyBarca specific features

- **Property timelining** any database property can be given a timeline: values are stored as date-ranged slots rather than a single value. Example: In Notion, you could only put "City A" in a relation property for a character in a world-building database, but if that location were to change, you wouldn't be able to see the previous cities the character resided in. With CapyBarca you can display a full history within a single property ("timelining a property"). Atm rollups and formulas will only use the most recent value but specific and broad querying is a planned feature.
- **Nuanced Relations**: relation properties are able to track qualifiers, so called "Nuances". Example: In Notion, you could only put "organisation X" into the relation property "organisation" in a character database. CapyBarca allows you to qualify that relation. Now you not only can set it to "**organisation X** *as* **rank A**", but you can combine nuancing and timelining, enabling you to track the career of your character within not only one, but any number of organisations. Of course, nuancing (and timelining) has a lot more nifty use cases.
- **Keyed Relations**: A rollup within a relation property, providing data from the target relation entries to sort them by. For example, a plot property normally suffers from all plot beats being sorted by seniority, i.e. the order you added them to the list. Your plot beats will show up in a non chronological order. By keying them to their own date properties, they will now be listed next to their respective dates in chronological order - or whatever order you like to apply.

---

## Requirements

- Linux server (x86-64)
- [Docker](https://docs.docker.com/engine/install/) with Compose v2
- [Tailscale](https://tailscale.com/download) connected and running, with HTTPS certificates enabled

CapyBarca is designed for private self-hosting over Tailscale. It uses Tailscale's HTTPS certificate infrastructure and is not intended to be exposed on the public internet. This is a deliberate safety layer as the software is still in pre release. A non tailscale option will be added by version 1.*, until then it is strongly advised to not strip the additional security layer that tailscale provides to your data stored within CapyBarca.

---

## Installation

```bash
git clone https://github.com/Klozeitung/capy_barca.git
cd capy_barca
chmod +x setup.sh
./setup.sh
```

`setup.sh` will:

1. Check for Docker and Tailscale
2. Walk through the configuration interactively (database credentials, ports, network)
3. Obtain an SSL certificate via Tailscale
4. Build and start all containers
5. Run database migrations and the test suite

On first start you will be prompted to create an admin account in the browser.

---

## Updating

```bash
./update.sh
```

This pulls the latest version from GitHub and re-runs `setup.sh` in update mode. Your `.env` and all data are preserved.

---

## Backup

Backups run from a separate machine over SSH/Tailscale and produce a portable `.capy` file containing the database dump, all uploads, and the configuration.

Download `backup.sh` from **Settings → Backup** inside the app, fill in the four variables at the top, and run it:

```bash
chmod +x backup.sh
./backup.sh
```

The `.capy` file can be used to fully restore CapyBarca on any server.

---

## Restore

Copy a `.capy` file into `recovery/import/` on your server, then run:

```bash
./restore.sh
```

This will stop the running instance, replace all data with the backup, and start CapyBarca again. A version mismatch warning is shown if the backup was created with a different version.

---

## Configuration

All configuration lives in `.env` (created by `setup.sh`, never committed). Run `setup.sh` again at any time to edit individual fields or reconfigure from scratch.

Key variables:

| Variable | Description |
|---|---|
| `POSTGRES_USER` / `POSTGRES_PASSWORD` / `POSTGRES_DB` | Database credentials |
| `PORT_FRONTEND` | Port CapyBarca is served on (default: 1701). Must be above 1023 — the containers run unprivileged and cannot bind privileged ports |
| `PORT_BACKEND` | Internal port the backend listens on (default: 17012). Reachable only inside the Compose network, never published to the host. Must be above 1023 for the same reason as `PORT_FRONTEND` |
| `PORT_DB` | Host-side port for local database maintenance. Published on `127.0.0.1` only; containers always talk to PostgreSQL on 5432 over the internal network |
| `SECRET_KEY` | Signing key for session cookies and, via a derived key, for the access tokens handed to Collabora. The single most valuable secret in the installation — keep it out of backups you share |
| `TAILSCALE_IP` / `TAILSCALE_HOSTNAME` | Your Tailscale network address |
| `ALLOW_NEW_USERS` | `true` to allow self-registration on the login page |
| `DEBUG` | Development only, default `false`. `true` starts uvicorn with `--reload` and issues the session cookie **without** the `Secure` flag. Leave it off on any real instance |
| `APP_UID` / `APP_GID` | The account the containers run as. Set automatically by `setup.sh` from the user running it, so that `static/uploads` stays writable from the container and from `restore.sh` alike. Not meant to be edited by hand |
| `FORWARDED_ALLOW_IPS` | Which peers may set `X-Forwarded-For`, used to identify clients for rate limiting. Default `*`, which is safe because the backend port is reachable only inside the Compose network and nginx overwrites the header. Narrow it to the nginx container address to exclude other containers as well |
| `MAX_UPLOAD_MB` | Largest single upload, in megabytes (default: 100). Enforced by nginx on the request and by the backend on the file, from this one value, so the two cannot disagree. Also caps what Collabora may write back when saving a document |
| `LOGIN_RATE_LIMIT` `SIGNUP_RATE_LIMIT` `PASSWORD_CHANGE_RATE_LIMIT` | Attempts per client for the three routes that hand out or verify credentials. Default `5/minute` each |
| `BOOKMARK_RATE_LIMIT` | Bookmark previews per client (default: `10/minute`). The bookmark endpoint makes the server fetch a URL you choose, so it is throttled rather than left open |
| `WOPI_TOKEN_RATE_LIMIT` | Collabora editor tokens per client (default: `30/minute`). Applies to token issuance only; the file endpoints Collabora calls during an editing session are not throttled |

---

## Tech Stack

- **Frontend**: Vue 3, TypeScript, Vite, vue-i18n, Pinia
- **Backend**: Python, FastAPI, SQLAlchemy, Alembic, PostgreSQL
- **Infrastructure**: Docker Compose, nginx, Collabora Online

All containers run as an unprivileged user rather than as root. `setup.sh` aligns the container account with the host account that owns the repository and adjusts the ownership of `ssl/` and `static/uploads/` accordingly. Run it as your normal user, not with `sudo`; it escalates on its own where that is required.

---

## Transparency

- **AI Usage Disclosure**: The Anthropic LLM "Claude" was used in creating this software.
- **System Testing**: CapyBarca is not yet tested for broad system compatability. "Runs fine on mine" is all the dev can say at this point.

---

## Project Status

This project is a work in progress. It is used by its developer since February 2026 as full replacement of Notion. Having been worked on in a private repo, it was copied to this public repository when it was deemed to be useful and stable enough.

As Timelining and Nuancing are rather complicated features, rollups and formulas will work without taking nuances into account and will default to "last state" for timelined properties - for now.

Despite heavy testing (~1750 automated backend tests on every push through CI and on every build in the dev environment by setup.sh, plus a lot of checklists to ensure nothing breaks in A when working on B or adding C) this project is not peer reviewed or community tested as of yet. Thus, this honest disclaimer is in order:

DO NOT USE FOR IRREPLACABLE DATA AND FILES. USE AT OWN RISK.

Please use the backup system as regularly as possible.

---

## License

CapyBarca is licensed under the **MIT License**.

You are free to use, copy, modify, merge, publish, distribute, sublicense, and sell copies of the software. The only requirement is that the copyright notice and license text are included in all copies or substantial portions of the software.

See [LICENSE](LICENSE) for the full license text.
