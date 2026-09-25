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

This repository intentionally contains no ChatGPT DOM controller, browser-response extraction, Tampermonkey controller, frontend shell, Chromium extension, or DOM-based model transport.

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
