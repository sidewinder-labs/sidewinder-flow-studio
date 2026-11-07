# sfs-plugin-tm1

IBM Planning Analytics (TM1) for Sidewinder Flow Studio, built on
[TM1py](https://github.com/cubewise-code/tm1py) (MIT). The user guide is the
*IBM Planning Analytics (TM1)* section of
[docs/CAPABILITIES.md](../../docs/CAPABILITIES.md); this file is for developers.

Included in the desktop app and the Docker images (`SFS_PLUGINS` default).

## What it registers

- Connection type **`tm1`** (`connection.py`): one form for v11 (TM1 security,
  CAM, Windows integrated) and v12 (as a Service, IBM Cloud, Engine on
  premises). `service_kwargs()` maps the stored fields to `TM1Service(**kwargs)`.
- **`tm1_read`** (*TM1 read*): MDX or a cube view → one row per cell.
- **`tm1_load`** (*TM1 load*): replace slice / add / overwrite, bulk, unknown
  elements, dry run (`cubes.py`).
- Example flow: *TM1 · Load sales actuals into a cube*.

Engine and dialect names are saved in flows and connections: never rename them.

## Tests

`pytest` here runs against a fake TM1 server (no TM1py calls leave the
process), plus a flow through the SFS executor when the backend is installed.
