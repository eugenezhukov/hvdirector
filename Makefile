# Developer shortcuts (production install: sudo ./deploy/install.sh)
PY ?= backend/.venv/bin/python

.PHONY: venv test ui ui-dev api worker

venv:
	python3 -m venv backend/.venv && backend/.venv/bin/pip install -e 'backend[dev]'

test:
	cd backend && ../$(PY) -m pytest -q

ui:
	cd frontend && npm ci && npm run build

ui-dev:
	cd frontend && npm run dev

# Dev servers use HVD_CONFIG_FILE (see deploy/hvdirector.env.example), e.g. with HVD_HYPERV_DRIVER=fake
api:
	cd backend && ../$(PY) -m uvicorn --factory hvdirector.main:create_app --host 127.0.0.1 --port 8000 --reload

worker:
	cd backend && ../$(PY) -m hvdirector.worker
