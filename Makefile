# Linux test entry points. Integration tests need Xvfb (and Xephyr for the Xinerama test): install `xvfb` and
# `xserver-xephyr`, or run `make xvfb-local` to unpack Xvfb into .tools/ without root.
PY ?= .venv/bin/python
PYTEST = $(PY) -m pytest -p no:cacheprovider -q
QUICK = -m "not slow and not soak"

.PHONY: test-quick test-all lint soak soak-full xvfb-local quick-core quick-gui quick-x11 build-linux

# Under a minute: everything except the slow marks, three pytest processes side by side.
test-quick:
	$(MAKE) -j3 --output-sync=target quick-core quick-gui quick-x11

quick-core:
	$(PYTEST) $(QUICK) --ignore=tests/test_gui_full.py --ignore=tests/test_ui.py --ignore=tests/test_x11_integration.py
quick-gui:
	$(PYTEST) $(QUICK) tests/test_gui_full.py tests/test_ui.py
quick-x11:
	$(PYTEST) $(QUICK) tests/test_x11_integration.py

# Everything: full Hypothesis runs, slow and integration tests, static checks, then the soak in compressed time.
test-all:
	HYPOTHESIS_PROFILE=all $(PYTEST) -m "not soak"
	$(MAKE) soak

lint:
	$(PY) -m ruff check .
	$(PY) -m mypy

soak:  # the 30-minute, 50-target schedule compressed 60x (about 35 s)
	$(PY) tests/soak.py --minutes 30 --fast 60
soak-full:  # the same in real time
	$(PY) tests/soak.py --minutes 30

# AppImage + .deb in dist/ (the built app is smoke-tested under Xvfb first). Windows builds come from CI:
# push a vX.Y.Z tag and .github/workflows/ci.yml attaches the installer, portable zip and Linux packages to a release.
build-linux:
	$(PY) build.py
	PY=$(PY) packaging/linux/build.sh

xvfb-local:
	mkdir -p .tools/deb .tools/xvfb
	cd .tools/deb && apt-get download xvfb
	dpkg -x .tools/deb/xvfb_*.deb .tools/xvfb
