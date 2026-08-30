PYTHON ?= python3

.PHONY: test install uninstall lint

test:
	$(PYTHON) -m unittest discover -s tests -v

install:
	sudo ./setup/install.sh

uninstall:
	sudo ./setup/uninstall.sh

lint:
	$(PYTHON) -m py_compile throtl/*.py throtl/gui/*.py
