# AGENTS.md — Instructions for AI Coding Agents

This document provides guidance for AI agents (Grok, Claude, Cursor, GitHub Copilot, Windsurf, etc.) working in the **forza-telemetry-fh6** repository.

## You Are an Agent With Execution Privileges

You are an autonomous coding agent. You have permission — and are **expected** — to take action in the development environment to complete the user's requests.

### Core Rule: Use `./dev.sh` to Manage Services

This project uses a single `dev.sh` script as the canonical way to control all development services.

**When the user asks you to restart components, launch services, fix a running system, iterate until something works, etc., you must use `./dev.sh` yourself.**

Do **not** respond with instructions like "now run `./dev.sh restart collector`" unless the user has explicitly told you not to touch running processes.

#### Common Commands You Should Use

```bash
./dev.sh status                 # See what is currently running
./dev.sh restart collector      # Restart just the UDP collector
./dev.sh restart api            # Restart the Flask API
./dev.sh restart frontend       # Restart the Vite dev server
./dev.sh restart dashboard      # (if supported) or use full restart
./dev.sh restart                # Restart the whole stack
./dev.sh logs collector         # Tail logs for one component
./dev.sh logs all               # Tail logs for everything important
./dev.sh stop <component>
./dev.sh start <component>
```

Components typically include: `collector`, `api`, `frontend`, `dashboard`, `mongo`.

### When You Should Act Proactively

You should use `./dev.sh` (and other shell commands) in these situations without waiting for explicit permission every time:

- After making changes to Python code, the collector or dashboard needs to be restarted for the changes to take effect.
- After editing `.env` (or creating `.env` from `.env.example`).
- After running `pip install` or updating dependencies.
- When the user says phrases like:
  - "restart the components yourself"
  - "launch it yourself and iterate until working"
  - "fix it and get it running"
  - "you look at the logs and restart as needed"
- When debugging live behavior (checking logs, status, etc.).

### Working With the Environment

- **Virtual environment**: Always activate the project's venv before running Python commands:
  ```bash
  source .venv/bin/activate
  ```
- **Package installation**: Run `pip install -r requirements.txt` (inside the activated venv) when dependencies change.
- **Configuration**: You are allowed to create or edit the real `.env` file (not just `.env.example`) when the user requests configuration changes (e.g. adding `FH6CARDATA_API`).
- **Verification**: After restarting services, use `./dev.sh status`, `curl`, log inspection, etc. to verify things are working.

### Philosophy

Be helpful and proactive. The user expects you to drive the development environment, not just edit code and give the user a list of manual steps.

If a task requires a service to be running with your latest changes, restart the relevant services using `./dev.sh` and confirm the result to the user.

Only ask the user to manually restart when they have previously told you not to touch running processes in the current conversation.

## Other Notes

- The project has both a Python backend (collector + API + dashboard) and a Vite/React frontend.
- The `dev.sh` script is the single source of truth for local development orchestration.
- You have full access to run terminal commands, read logs, inspect processes, and manage the environment via the tools available to you.

---

**Last updated**: When the agent was explicitly instructed to document its ability to manage dev services via `dev.sh`.