# CapyBarca

CapyBarca is a self-hosted personal knowledge management application. It runs entirely on your own infrastructure, requires no cloud account, and keeps your data under your control.

> Licensed under [MIT](LICENSE). Copyright (c) 2026 Klozeitung.

---

## Features

- **Pages and blocks** — hierarchical workspace with rich-text pages, headings, lists, code blocks, and file attachments
- **Databases** — structured tables with custom property types (text, select, date, relation, formula, rollup, and more), filters, sorting, and multiple views per database
- **Property timelining** — any database property can be given a timeline: values are stored as date-ranged slots rather than a single value, so the full history of how a property changed over time is preserved and queryable. Each slot has a configurable start and end date; open-ended ranges, "always valid" entries, and relation pools with per-linked-entry date ranges are all supported. Columns can display the current value, all slots, or a value at a specific point in time — making it possible to model things like role changes, status histories, or time-bound relations directly in a database without external tooling.
- **Automations** — rule-based triggers that act on database entries
- **Comments** — inline comments on pages and database entries
- **Collaborative editing** — real-time document collaboration via integrated Collabora Online (WOPI)
- **Backup and restore** — portable `.capy` backup files covering database, uploads, and configuration; full restore via a single script
- **Multi-user** — admin and member roles, optional self-registration

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

## AI Usage Disclosure

The Anthropic LLM "Claude" was used in creating this software.

---

## Project Status

This project is a work in progress. It is used by its developer since February 2026 as full replacement of Notion.

---

## License

CapyBarca is licensed under the **MIT License**.

You are free to use, copy, modify, merge, publish, distribute, sublicense, and sell copies of the software. The only requirement is that the copyright notice and license text are included in all copies or substantial portions of the software.

See [LICENSE](LICENSE) for the full license text.
