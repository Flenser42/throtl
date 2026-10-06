# Standard interpreter for tests/lint (no GUI dependencies anymore).
PYTHON ?= python3
# ruff: prefer the installed binary, otherwise via uvx (no pip needed).
RUFF ?= $(shell command -v ruff >/dev/null 2>&1 && echo ruff || echo "uvx ruff")

.PHONY: test lint lint-fix contrast check build clean install install-app uninstall deb app-build

test:
	$(PYTHON) -m unittest discover -s tests -v

lint:
	$(RUFF) check .

lint-fix:
	$(RUFF) check --fix .

# WCAG contrast of the design tokens (standard library, no display needed).
contrast:
	$(PYTHON) tools/check_contrast.py

check: lint contrast test

build:
	$(PYTHON) -m build

deb:
	bash packaging/deb/build.sh

clean:
	rm -rf build dist *.egg-info .ruff_cache .mypy_cache
	find . -type d -name __pycache__ -prune -exec rm -rf {} +

# Full install: daemon (root service) + the new GUI (per-user).
install:
	sudo ./setup/install.sh

# GUI only (no root; builds the AppImage if needed).
install-app:
	./setup/install-app.sh

# Just build the Tauri bundles (.deb/.rpm/.AppImage).
app-build:
	cd throtl-app && npm run tauri build

uninstall:
	sudo ./setup/uninstall.sh
