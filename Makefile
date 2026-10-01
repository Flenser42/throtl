# Standard-Interpreter fuer Tests/Lint (keine GUI-Abhaengigkeiten mehr).
PYTHON ?= python3
RUFF ?= ruff

.PHONY: test lint lint-fix check build clean install install-app uninstall deb app-build

test:
	$(PYTHON) -m unittest discover -s tests -v

lint:
	$(RUFF) check .

lint-fix:
	$(RUFF) check --fix .

check: lint test

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
