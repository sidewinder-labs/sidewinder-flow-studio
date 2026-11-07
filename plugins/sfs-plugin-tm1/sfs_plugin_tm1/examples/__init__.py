"""Example flow shipped with the TM1 plugin: CSV → transform → load into a cube.

It needs a TM1 connection named "tm1" (Meta → Connections → IBM Planning
Analytics) and a cube to load into; the load node starts in dry-run mode, so
running it only checks the elements and writes nothing.
"""
from __future__ import annotations

READ_CODE = """\
# Monthly sales actuals shipped with the TM1 plugin. For your own file:
#   df = pd.read_csv('/data/imports/actuals.csv')
from importlib.resources import files

df = pd.read_csv(files('sfs_plugin_tm1.examples') / 'sales_actuals.csv')
"""

SHAPE_CODE = """\
# One row per cell: a column per cube dimension, then the value.
# Rename the columns to your cube's dimension names (or map them on the load node).
df = df.melt(id_vars=["region", "product", "month"], value_vars=["units", "revenue"],
             var_name="measure", value_name="value")
df = df.rename(columns={"region": "Region", "product": "Product", "month": "Month",
                        "measure": "Sales Measure"})
df["Version"] = "Actual"
df = df[["Version", "Region", "Product", "Month", "Sales Measure", "value"]]
"""


def _node(node_id, node_type, x, **data):
    return {"id": node_id, "type": node_type, "position": {"x": x, "y": 200}, "data": data}


def _edge(a, b):
    return {"id": f"e-{a}-{b}", "source": a, "target": b, "sourceHandle": "out", "targetHandle": "in"}


EXAMPLES = [(
    "TM1 · Load sales actuals into a cube",
    "CSV → reshape to one row per cell → TM1 load (replace slice, dry run first). "
    "Needs a TM1 connection named tm1 and a Sales cube.",
    {
        "name": "Load actuals",
        "nodes": [
            _node("start", "trigger", 40, label="Start", run_mode="manual", cron="", timezone="UTC"),
            _node("read", "processor", 300, label="Sales actuals (CSV)", engine="pandas", code=READ_CODE),
            _node("shape", "processor", 620, label="One row per cell", engine="pandas", code=SHAPE_CODE),
            _node("load", "processor", 940, label="Load into Sales", engine="tm1_load", code="",
                  connection="tm1", cube="Sales", mode="replace_slice", value_column="value",
                  unknown_elements="fail", dry_run=True),
        ],
        "edges": [_edge("start", "read"), _edge("read", "shape"), _edge("shape", "load")],
    },
)]
