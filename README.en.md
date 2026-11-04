# HV Director

A multi-tenant self-service portal for Microsoft Hyper-V clusters — a simplified take on VMware Cloud
Director. The provider connects failover clusters or standalone hosts, defines storage classes and creates
organizations (tenants) with quotas and VLAN pools. Tenant users create VMs and networks, upload ISO
images, take snapshots and open VM consoles in the browser.

*Русская версия: [README.md](README.md)*

## Features

* **Add cluster / host wizard** over WinRM: detects a failover cluster or a standalone host, lists nodes
  with their IPs, CSVs and virtual switches; **Refresh** re-polls the membership and reports added and
  removed nodes.
* **Tenants** bound to a cluster with quotas: vCPU, RAM, number of VMs, ISO storage, number of snapshots
  and **disk space per storage class** (Gold / Silver / … mapped to CSV or SMB3 datastores).
* **Local user database** per tenant with roles `tenant_admin`, `audit`, `console`, assigned
  organization-wide or **delegated on a single VM**.
* **VM lifecycle**: create (generation 1/2, Secure Boot, disks per class, NICs, ISO), power on, guest
  shutdown, power off, guest restart, reset, snapshots (take / revert / delete), insert/eject ISO,
  change vCPU/RAM, add disks and adapters, delete.
* **Placement**: the node with the most free memory; in a cluster the VM is registered as a
  highly-available role. VMs moved by live migration or failover are located automatically.
* **Networking**: the provider assigns VLAN ranges of a pre-built L2 underlay to a tenant (no overlaps);
  a tenant network takes one VLAN and VM adapters are set to access mode on that VLAN.
* **ISO library** on `\\shared\iso`: chunked upload straight to SMB, file names prefixed with a GUID,
  public catalog of the provider.
* **Console**: guacamole-common-js in an iframe popup; the backend tunnels the WebSocket to guacd, which
  speaks RDP in `vmconnect` mode to port 2179 of the node owning the VM. Host credentials never reach the
  browser; one-time tickets.
* **Virtual routers (Edge)**: a Debian 13 VM per organization installed unattended from the provider ISO;
  management in the global table (SSH from HV Director), WAN and tenant networks in a VRF, networks attached
  as VLAN SVIs on a trunk; stateful/stateless firewall, SNAT/DNAT, IP sets, explicit TCP/UDP ports.
  See [docs/routers.md](docs/routers.md) (Russian).
* **UI in Russian and English**, task panel and audit log.
* **Everything on one Debian 13 machine without Docker**; host addresses and credentials live in
  PostgreSQL (credentials encrypted with a master key).

## Install (Debian 13)

```bash
sudo ./deploy/install.sh --hostname hvdirector.example.com
```

The script installs PostgreSQL, nginx, a Python virtual environment, builds guacd 1.6.0 with FreeRDP 3,
generates `/etc/hvdirector/hvdirector.env`, migrates the database, creates the first provider
administrator (password printed at the end), issues a self-signed certificate and enables
`hvdirector-api`, `hvdirector-worker`, `hvdirector-guacd`. Re-run it to upgrade. `--demo` uses the
built-in Hyper-V simulator.

Prepare the Hyper-V hosts as described in [docs/hyperv-setup.md](docs/hyperv-setup.md) (Russian):
WinRM, firewall for port 2179, a virtual switch with the same name on every node, share permissions for
`\\shared\iso` and CredSSP if attaching ISO images hits the double-hop problem.

## Development

```bash
cd backend && python3 -m venv .venv && .venv/bin/pip install -e '.[dev]' && .venv/bin/pytest
```

```bash
cd frontend && npm ci && npm run dev
```

`HVD_HYPERV_DRIVER=fake` runs the whole portal against a Hyper-V simulator.
