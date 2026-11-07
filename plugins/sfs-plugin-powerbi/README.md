# sfs-plugin-powerbi

Power BI semantic model refresh for Sidewinder Flow Studio, with the
[enhanced refresh API](https://learn.microsoft.com/power-bi/connect-data/asynchronous-refresh)
and an Entra ID service principal (MSAL client credentials). The user guide is
the *Power BI refresh* section of [docs/CAPABILITIES.md](../../docs/CAPABILITIES.md).

Included in the desktop app and the Docker images (`SFS_PLUGINS` default).

## What it registers

- Connection type **`powerbi`**: tenant ID, client ID, client secret (secret).
- **`powerbi_refresh`** (*Power BI refresh*): checks for a refresh already
  running, starts one (type, commit mode, parallelism, retries, tables and
  partitions from a list or upstream columns), polls it, and returns a status
  frame with one row per object (`refresh.py`).

## Service principal setup

1. Register an app in Entra ID and create a client secret.
2. Power BI admin portal → Developer settings → *Service principals can use
   Fabric APIs*: enable it for the app (or a security group holding it).
3. Add the app to each workspace as **Member** or **Admin**.
4. The workspace must be on Premium, Premium Per User, Fabric or Embedded
   capacity: enhanced refresh isn't available on shared capacity.

## Tests

`pytest` here runs against a simulated Power BI REST API (httpx
`MockTransport`), plus a flow through the SFS executor when the backend is installed.
