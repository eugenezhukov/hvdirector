# HV Director — notes for Claude Code

Multi-tenant self-service portal for Microsoft Hyper-V clusters (simplified VMware Cloud Director): FastAPI +
PostgreSQL backend, Vue 3 + Element Plus UI (ru/en), worker with a PostgreSQL task queue, Hyper-V over WinRM/PSRP
(PowerShell scripts), VM console through guacd (RDP vmconnect :2179), virtual routers (Debian 13 VMs with nftables,
VRF, DHCP) configured over SSH.

**Read [HANDOVER.md](HANDOVER.md) first** — project state, what was never run against real Hyper-V, the live-cluster
test plan and the debugging workflow. Docs: README.md, docs/hyperv-setup.md, docs/routers.md.

## Commands

```bash
python3 -m venv backend/.venv && backend/.venv/bin/pip install -e 'backend[dev]'
export HVD_CONFIG_FILE=~/hvd/dev.env          # see HANDOVER.md section 2
backend/.venv/bin/hvdirector-manage migrate
make api                                      # 127.0.0.1:8000
make worker
cd backend && .venv/bin/python -m pytest -q   # needs PostgreSQL database hvdirector_test
cd frontend && npx vite build                 # frontend/dist is committed: rebuild + commit with UI changes
backend/.venv/bin/hvdirector-manage hv-run <script> --cluster "<name>" --host <node> --args '<json>'
```

## Rules of this codebase

* Every Hyper-V operation exists three times: `hyperv/base.py` (interface), `hyperv/psrp.py` + `hyperv/scripts/*.ps1`
  (real), `hyperv/fake.py` (simulator used by tests and demos) — keep them in sync.
* PowerShell scripts get one JSON argument (`$A`), never string interpolation; return a hashtable;
  `Fail 'CODE' 'message'` for expected refusals; `foreach ($x in $A.list)` without `@()`.
* User-facing errors are `AppError(code, message, **params)`; add `errors.<code>` to both
  `frontend/src/i18n/ru.js` and `en.js`. All UI text goes through i18n in both languages.
* Schema changes: `hvdirector-manage makemigration -m ...`, then rename to `000N_<name>.py` with `revision = '000N'`.
* Changes to a VM reserve quota in the API (`services/vmconfig.py`) and are undone by the task's `@on_error` hook.
* Credentials live in the database encrypted with `HVD_SECRET_KEY`; never log or print them.
* Live cluster: test in a dedicated test organization; do not run state-changing scripts
  (`vm_create`, `vm_reconfigure`, `vm_delete`, …) with `hv-run` against production VMs.
