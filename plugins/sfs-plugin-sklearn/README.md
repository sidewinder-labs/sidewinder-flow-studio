# sfs-plugin-sklearn

A working example of an **SFS plugin** — adds a `scikit-learn` execution engine
to Sidewinder Flow Studio. Use it as a template for your own engine.

## What it demonstrates

- Declaring a plugin via the `sfs.plugins` entry point (`pyproject.toml`)
- Implementing `register(api)` and an `EngineSpec`
- Writing a `run(EngineContext)` that honours the SFS contract: the flow
  preamble, multi-input frames (`df2`, `df3`), empty-code pass-through, custom
  options from `node_data`, and not mutating `exec_globals`
- Lazy importing the heavy dependency so SFS starts fine without it installed

## Install

When it is in the official catalog, install it from the **Plugins** menu. To
build it into a Docker image, list it in `SFS_PLUGINS` in `.env` (copied from
`.env.docker`) and rebuild the backend image:

```bash
SFS_PLUGINS=curvefit,sklearn
docker compose build --no-cache backend && docker compose up -d
```

The engine picker will show **scikit-learn** with a 🧩 plugin
badge — no frontend change needed, because the UI renders from
`GET /api/capabilities`.

Verify from the command line:

```bash
curl -sL localhost:8080/api/capabilities/ | python -m json.tool | grep -A3 sklearn
```

## Using it

Set a processor node's engine to **scikit-learn**. `df` is the upstream frame;
`sklearn`, `np`, `train_test_split` and `quick_model()` are pre-injected.

One-liner:

```python
df, metrics = quick_model(df, target="churned")
print(metrics)   # {'model': 'rf_classifier', 'accuracy': 0.94, ...}
```

Full control:

```python
from sklearn.ensemble import GradientBoostingClassifier
X = df[["tenure", "monthly_spend", "support_tickets"]]
y = df["churned"]
model = GradientBoostingClassifier().fit(X, y)
df = df.assign(prediction=model.predict(X))
```

`quick_model(df, target, features=None, kind=None, test_size=0.2, **model_kwargs)`
picks a classifier or regressor from the target's dtype/cardinality unless you
pass `kind` (`rf_classifier`, `rf_regressor`, `logistic`, `linear`). Non-numeric
feature columns are dropped — encode them upstream if you need them.

As with every engine, **only `df` passes downstream**, so leave your result there.

## The isolated variant

The package registers two engines from the same code:

- **scikit-learn** — runs in SFS's process (fast, shares the environment)
- **scikit-learn (isolated)** — runs `runner.py` inside a separate virtual
  environment; SFS hands the frame over as an Arrow file and never imports
  scikit-learn

To use the isolated one, build its environment into the Docker image and tell SFS where environments live:

```dockerfile
# in backend/Dockerfile
RUN python -m venv /opt/sfs-venvs/sklearn \
 && /opt/sfs-venvs/sklearn/bin/pip install --no-cache-dir pyarrow pandas scikit-learn
ENV SFS_PLUGIN_VENVS_DIR=/opt/sfs-venvs
```

The node's **Plugin environment** field takes the folder name (`sklearn`) or an
absolute path. Everything else — the target dropdown, the model selector, the
code cell — works identically; your code simply executes in the other
interpreter.

## A note on isolation

This plugin runs **in-process**: scikit-learn is installed into SFS's own
environment. That's fine for scikit-learn, which coexists with the core stack.

Libraries with aggressive version pins — PyCaret is the usual offender — should
use the planned **isolated runtime** instead, where the library lives in its own
virtual environment and frames cross as Arrow files, so the core environment is
never polluted. The registration contract is identical; only `run` differs (it
becomes a thin client that invokes the runtime rather than importing the library).

## Contract reference

See [docs/PLUGINS.md](../../docs/PLUGINS.md).
