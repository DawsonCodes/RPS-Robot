# Contributing

Thanks for your interest in RPS-Robot! This is a personal portfolio project,
but suggestions and fixes are welcome.

## Getting started

1. Fork and clone the repository.
2. Run `./setup.sh` to create the virtual environment and install dependencies.
3. You can develop and test without any hardware using demo mode:
   ```bash
   RPS_DEMO_MODE=true ./run.sh
   ```

## Guidelines

- Keep changes small and focused.
- Match the existing code style and naming.
- Run a quick syntax check before opening a pull request:
  ```bash
  python -m compileall app.py
  ```
- Describe what you changed and why in your pull request.

## Reporting issues

Open an issue with steps to reproduce, what you expected, and what happened.
If it's hardware-related, include your Raspberry Pi model, OS version, and camera.
