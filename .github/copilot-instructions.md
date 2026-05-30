# GitHub Copilot Instructions

This repository uses `AGENTS.md` (in the root) as the primary source of guidance for AI coding agents.

**Please read the full `AGENTS.md` file at the root of this repository.**

Key points for working in this project:

- You are an agent and are expected to actively manage the development environment.
- Use `./dev.sh` (the project's dev orchestration script) to start, stop, restart, and inspect services (`collector`, `api`, `frontend`, etc.) as needed.
- When the user asks you to "restart the components yourself", "get it running", "iterate until it works", etc., you should use `./dev.sh restart ...` (and related commands) directly rather than just telling the user what to run.
- You may edit `.env`, run `pip install` inside the `.venv`, inspect logs, and take other operational actions to complete tasks.

See the full `AGENTS.md` for detailed commands and philosophy.