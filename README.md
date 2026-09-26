# PASI Engineering Workspace

A clean, roadmap-driven PASI automation workspace.

## Architecture

Roadmap / Issues
       |
       v
Operation State
       |
       v
Planner
       |
       v
ModelProvider
       |
       +--> PASI local API --> Ollama / local model
       |
       +--> future hosted provider

Computer capabilities stay separate:
Planner -> typed ComputerCapability -> host application

The repository contains a deliberately bounded Chromium ChatGPT handoff extension under `extensions/pasi-chatgpt/`. The extension is a typed browser boundary for authenticated task handoff, chat reuse/switching, usage-limit recovery, Thinking-state enforcement, checkpoints, and completion evidence. It does not use the DOM as a model transport or fabricate completion evidence.

## Local model API

Set the local model:

export PASI_OLLAMA_MODEL='nemotron-3-nano:4b'
python -m pasi.providers.local_api

The PASI API listens on 127.0.0.1:8787 by default.

## Development

python -m pip install -e '.[dev]'
python -m pytest -q

## Engineering rules

- Keep model inference independent from computer control.
- Keep operation identity and recovery state durable and bounded.
- Prefer local/free providers when they meet requirements.
- Require evidence for completion claims.
- Make one coherent change in one pull request.
- Do not rebuild the previous DOM/frontend architecture.
