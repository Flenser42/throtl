# Releasing Throtl

This document describes how to cut a release. Throtl is distributed primarily
as a source tree installed via `setup/install.sh`; wheels/sdists are built for
completeness and future packaging.

## 1. Pre-flight

```bash
make check          # ruff + full unittest suite
make build          # produces dist/throtl-<version>.tar.gz and .whl
```

Optional but recommended with a real display session (so the GUI widget tests
run instead of being skipped):

```bash
python3 -m unittest discover -s tests -v
```

## 2. Version

`throtl.__version__` (in `throtl/__init__.py`) is the single source of truth;
`pyproject.toml` reads it dynamically. Update it, then add a matching entry to
`CHANGELOG.md` and update the compare links at the bottom of that file.

## 3. Tag

```bash
git add -A
git commit -m "Release vX.Y.Z"
git tag -a vX.Y.Z -m "Throtl vX.Y.Z"
git push origin master
git push origin vX.Y.Z
```

## 4. Publish

- **GitHub**: create the release from the tag and paste the `CHANGELOG.md`
  section. Attach the artifacts from `dist/` if desired.
- **PyPI** (optional):

  ```bash
  python -m build
  python -m twine upload dist/*
  ```

  Note that the GUI requires the *system* PyGObject; installing from PyPI only
  provides the Python modules and the `throtl-cli` / `throtl-daemon` /
  `throtl-gui` entry points.

## 5. Verify the installer

On a clean Arch/Omarchy machine:

```bash
sudo ./setup/install.sh
systemctl status netlimiter-clone
throtl-cli status
```
