![alt text](https://github.com/Klozeitung/capy_barca/blob/main/frontend/public/CapyBarca.png "CapyBarca Logo")

# CapyBarca

CapyBarca is a self-hosted personal knowledge management application. It runs entirely on your own infrastructure, requires no cloud account, and keeps your data under your control.

> Licensed under [MIT](LICENSE). Copyright (c) 2026 Klozeitung.

---

## Features

- **Pages and blocks** hierarchical workspace with rich-text pages, headings, lists, code blocks, and file attachments
- **Databases** structured tables with custom property types (text, select, date, relation, formula, rollup, and more), filters, sorting, and multiple views per database
- **Property timelining** any database property can be given a timeline: values are stored as date-ranged slots rather than a single value. Example: In Notion, you could only put "City A" in a relation property for a character in a world-building database, but if that location were to change, you wouldn't be able to see the previous cities the character resided in. With CapyBarca you can display a full history within a single property ("timelining a property"). Atm rollups and formulas will only use the most recent value but specific and broad querying is a planned feature.
- **Automations** rule-based triggers that act on database entries
- **Comments** inline comments on pages and database entries
- **Collaborative editing** real-time document collaboration via integrated Collabora Online (WOPI)
- **Backup and restore** portable `.capy` backup files covering database, uploads, and configuration; full restore via a single script
- **Multi-user** admin and member roles, optional self-registration
- **Supported Languages**: English, German

---

## Requirements

- Linux server (x86-64)
- [Docker](https://docs.docker.com/engine/install/) with Compose v2
- [Tailscale](https://tailscale.com/download) connected and running, with HTTPS certificates enabled

CapyBarca is designed for private self-hosting over Tailscale. It uses Tailscale's HTTPS certificate infrastructure and is not intended to be exposed on the public internet.

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
| `PORT_FRONTEND` | Port CapyBarca is served on (default: 1701) |
| `SECRET_KEY` | Session signing key — keep this secret |
| `TAILSCALE_IP` / `TAILSCALE_HOSTNAME` | Your Tailscale network address |
| `ALLOW_NEW_USERS` | `true` to allow self-registration on the login page |

---

## Tech Stack

- **Frontend**: Vue 3, TypeScript, Vite, vue-i18n, Pinia
- **Backend**: Python, FastAPI, SQLAlchemy, Alembic, PostgreSQL
- **Infrastructure**: Docker Compose, nginx, Collabora Online

---

## Transparency

- **AI Usage Disclosure**: The Anthropic LLM "Claude" was used in creating this software.
- **System Testing**: CapyBarca is not yet tested for broad system compatability. "Runs fine on mine" is all the dev can say at this point.

---

## Project Status

This project is a work in progress. It is used by its developer since February 2026 as full replacement of Notion. Having been worked on in a private repo, it was copied to a (this) public repository when it was deemed to be useful and stable enough.

Despite heavy testing (~1430 automated backend tests each time a new version is build in the dev environment by setup.sh and a lot of checklists to ensure nothing breaks in A when working on B or adding C) this project is not peer reviewed or community tested as of yet. Thus, this honest disclaimer is in order:

DO NOT USE FOR IRREPLACABLE DATA AND FILES. USE AT OWN RISK.

Please use the backup system as regularly as possible.

---

## License

CapyBarca is licensed under the **MIT License**.

You are free to use, copy, modify, merge, publish, distribute, sublicense, and sell copies of the software. The only requirement is that the copyright notice and license text are included in all copies or substantial portions of the software.

See [LICENSE](LICENSE) for the full license text.
