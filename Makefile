PY := .venv/bin/python

.PHONY: run bot dashboard setup setup-dev test

# run bot + dashboard together (Ctrl-C stops both)
run:
	$(MAKE) -j2 bot dashboard

bot:
	$(PY) -m trader.main

dashboard:
	$(PY) -m trader.dashboard

setup:
	python3 -m venv .venv
	$(PY) -m pip install -r requirements.txt

# Fully offline — no Alpaca calls, writes to a tmp log dir, safe while the bot runs
test:
	$(PY) -m pytest

setup-dev:
	$(PY) -m pip install -r requirements-dev.txt
