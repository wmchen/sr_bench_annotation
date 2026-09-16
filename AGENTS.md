# Repository Guidelines

## Project Structure & Module Organization

This repository extends X-AnyLabeling, a Python/PyQt6 desktop annotation tool, with Real-ISR workflows.

- `anylabeling/app.py` provides the application entry point.
- `anylabeling/views/labeling/` contains annotation UI and dataset logic; `realisr_dataset.py` handles persistence and HR/LR geometry synchronization without Qt dependencies.
- `anylabeling/services/` contains auto-labeling and training integrations.
- `anylabeling/configs/` stores YAML configuration; `anylabeling/resources/` holds icons and translations.
- `tests/` groups tests by area, including labeling, widgets, canvas, and models.
- `assets/` and `examples/` contain sample data and workflows; `docs/en/` and `docs/zh_cn/` contain documentation. Build helpers live in `scripts/` and `packaging/`.

## Build, Test, and Development Commands

Use Python 3.11 or newer; Python 3.12 matches CI. Run commands from the repository root.

- `pip install -e ".[cpu,dev]"` installs an editable CPU development environment. Choose a matching GPU extra instead when needed; do not install CPU and GPU ONNX runtimes together.
- `xanylabeling` launches the desktop application.
- `QT_QPA_PLATFORM=offscreen pytest` runs tests without a display on Linux.
- `pytest tests/test_labeling/test_realisr_dataset.py` runs focused dataset tests.
- `bash scripts/format_code.sh` formats code with Black.
- `flake8 anylabeling/` checks Python style.
- `python -m build` creates source and wheel distributions.
- `bash scripts/build_executable.sh linux-cpu` builds a Linux CPU executable with PyInstaller.

## Coding Style & Naming Conventions

Use four-space indentation, `snake_case` for functions/modules, and `PascalCase` for classes. Black targets Python 3.11 with 79-character lines; Flake8 settings live in `.flake8`. Follow `CONTRIBUTING.md`: add type hints to function definitions and Google-style docstrings to new functions and classes. Avoid manually editing generated `anylabeling/resources/resources.py`.

## Testing Guidelines

Tests use pytest, often with `unittest.TestCase`; name files `test_*.py` and methods `test_*`. Add regression coverage for changed behavior, especially annotation persistence, coordinate scaling, and UI interactions. Use temporary datasets and mocks where appropriate. No numeric coverage threshold is configured. CI currently runs `pytest --ignore=tests/test_widgets/test_toolbar_layout.py`; report skipped tests and missing optional dependencies.

## Commit & Pull Request Guidelines

Recent commits use concise, descriptive Chinese messages without a fixed prefix convention. Keep commits focused and identify the affected behavior. PRs should explain the problem, changes, and validation; link relevant issues and include screenshots for visible UI changes. Complete the CLA checkbox in `.github/PULL_REQUEST_TEMPLATE.md` after reviewing `CLA.md`.
