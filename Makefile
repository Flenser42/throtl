# System-Python bevorzugen: nur /usr/bin/python3 hat PyGObject (gi) fuer die GUI.
PYTHON ?= $(shell test -x /usr/bin/python3 && echo /usr/bin/python3 || echo python3)
RUFF ?= ruff

.PHONY: test lint lint-fix check build clean install uninstall

test:
	$(PYTHON) -m unittest discover -s tests -v

lint:
	$(RUFF) check .

lint-fix:
	$(RUFF) check --fix .

check: lint test

build:
	$(PYTHON) -m build

clean:
	rm -rf build dist *.egg-info .ruff_cache .mypy_cache
	find . -type d -name __pycache__ -prune -exec rm -rf {} +

install:
	sudo ./setup/install.sh

uninstall:
	sudo ./setup/uninstall.sh
