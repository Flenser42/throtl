# System-Python bevorzugen: nur /usr/bin/python3 hat PyGObject (gi) fuer die GUI.
PYTHON ?= $(shell test -x /usr/bin/python3 && echo /usr/bin/python3 || echo python3)

.PHONY: test install uninstall lint

test:
	$(PYTHON) -m unittest discover -s tests -v

install:
	sudo ./setup/install.sh

uninstall:
	sudo ./setup/uninstall.sh

lint:
	$(PYTHON) -m py_compile throtl/*.py throtl/gui/*.py
