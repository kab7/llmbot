# Deployment Notes

- GitHub repository: `kab7/llmbot` (`https://github.com/kab7/llmbot`)
- Former deployment server: `root@146.103.100.159` (reported unavailable on 2026-10-13).
- Primary deployment server: `root@195.63.141.49` (`amnezia2`), SSH port 22,
  key authentication. Do not deploy to the former host by default.
- Project path on server: `/root/llmbot`
- Current deployment style: Docker container started from `/root/llmbot`
- Docker Compose command on the primary server: `docker compose`.
- The primary server also runs VPN and Telegram-proxy containers. Scope deployment
  commands to llmbot; preserve those unrelated services.
- Fresh-host recovery runbook: `docs/DEPLOYMENT.md`.

# Repository Guidance

- Before changing runtime behavior, read `docs/AI_DEVELOPMENT.md`.
- Treat Python code as the behavioral source of truth.
- Keep `docs/AI_DEVELOPMENT.md`, `docs/schemas/*.json`, user-facing docs, and
  tests synchronized with code in the same change.
- Use `env.example` as the canonical environment-variable inventory.
- Repair/install the local environment with `./setup.sh --dev`.
- Run `venv/bin/python -m pytest` with Python 3.11-3.13 before handoff.
- Do not read, print, or commit `.env`, ChatGPT OAuth credentials, Telethon
  session files, SQLite runtime data, or `llm_traffic.log` unless the task
  explicitly requires inspecting that runtime data.
