# Plugins

Engine plugins for SFS. Each subfolder is a standalone installable Python
package that registers one or more execution engines through the
[plugin contract](../docs/PLUGINS.md) — no SFS code changes required.

| Plugin | Engine | Status |
|---|---|---|
| [sfs-plugin-sklearn](sfs-plugin-sklearn) | `scikit-learn` (in-process) and `scikit-learn (isolated)` | reference implementation |
| [sfs-plugin-curvefit](sfs-plugin-curvefit) | `Curve fit`, `Evaluate model` (lmfit), chart template *Fit with residuals* | in the desktop app and Docker images; see [docs/CURVE_FITTING.md](../docs/CURVE_FITTING.md) |
| [sfs-plugin-tm1](sfs-plugin-tm1) | `TM1 read`, `TM1 load` (TM1py), connection type `tm1` | in the desktop app and Docker images |
| [sfs-plugin-powerbi](sfs-plugin-powerbi) | `Power BI refresh` (enhanced refresh API), connection type `powerbi` | in the desktop app and Docker images |

## Installing

The desktop app and the Docker images include **curvefit**, **tm1** and **powerbi**;
newer versions and other official plugins install from the **Plugins** menu
(signed packs, see [docs/PLUGINS.md](../docs/PLUGINS.md#official-plugin-packs)).
To build one into a Docker image instead, list it in `SFS_PLUGINS` in `.env`
and rebuild the backend:

```bash
SFS_PLUGINS=curvefit,tm1,powerbi,sklearn
docker compose build --no-cache backend && docker compose up -d
```

The engine appears in the node picker with a 🧩 badge. The UI renders from
`GET /api/capabilities`, so no frontend change is needed. To develop a plugin,
see [docs/PLUGINS.md](../docs/PLUGINS.md).

## Writing your own

Copy `sfs-plugin-sklearn` — it's a complete working example of the contract
(entry point, `register(api)`, `EngineSpec`, `run(EngineContext)`, declared
parameters, lazy dependency imports). See [docs/PLUGINS.md](../docs/PLUGINS.md).

## In-process vs isolated

Plugins here run **in-process**: their dependencies install into SFS's own
environment. That is fine for libraries that coexist with the core stack
(scikit-learn does).

Libraries with aggressive version pins — PyCaret is the usual offender — should
use the **isolated runtime** instead: the library lives in its own virtual
environment and frames cross as Arrow files, leaving the core environment
untouched. The registration contract is the same; only the engine's `run`
differs (it calls `api.run_isolated(...)`).

`sfs-plugin-sklearn` ships both variants so you can compare them:

| Engine | Where it runs |
|---|---|
| `sklearn` | in-process, shares SFS's environment |
| `sklearn_isolated` | separate venv via `runner.py`, Arrow handoff |

Build the environment into the Docker image once:

```dockerfile
# in backend/Dockerfile
RUN python -m venv /opt/sfs-venvs/sklearn \
 && /opt/sfs-venvs/sklearn/bin/pip install --no-cache-dir pyarrow pandas scikit-learn
ENV SFS_PLUGIN_VENVS_DIR=/opt/sfs-venvs
```
