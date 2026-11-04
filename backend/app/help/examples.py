"""Ready-to-insert examples for the help panel.

Each example is the code of one node, in one engine. The help panel inserts it
into the selected node's editor (offering to switch the node's engine when it
differs). tests/test_help.py executes every example in its engine, so they keep
working: a connection called "my_db" and files under data/ stand in for yours.

`needs` says what the example expects upstream: "df" (a frame of sales rows),
"df2" (a second input, products), or nothing for a source node.
"""
from __future__ import annotations

TOPICS = (
    ("read-csv", "Read a CSV file"),
    ("read-folder", "Read many files from a folder"),
    ("read-database", "Read from a database"),
    ("write-database", "Write to a database"),
    ("transform", "Clean and summarise"),
    ("inputs", "Several inputs: join and stack"),
    ("vars", "Use a variable"),
    ("chart", "Make a chart"),
)

EXAMPLES: tuple[dict, ...] = (
    # ── Read a CSV ───────────────────────────────────────────────────────────
    {"id": "csv-pandas", "topic": "read-csv", "engine": "pandas", "title": "pandas",
     "code": 'df = pd.read_csv("data/sales.csv", parse_dates=["date"])\n'},
    {"id": "csv-polars", "topic": "read-csv", "engine": "polars", "title": "Polars (lazy)",
     "note": "scan_csv reads nothing until SFS runs the plan, and then only the columns it needs.",
     "code": 'df = pl.scan_csv("data/sales.csv", try_parse_dates=True)\n'},
    {"id": "csv-duckdb", "topic": "read-csv", "engine": "duckdb", "title": "DuckDB SQL",
     "code": "SELECT * FROM read_csv_auto('data/sales.csv')\n"},

    # ── Many files ───────────────────────────────────────────────────────────
    {"id": "folder-pandas", "topic": "read-folder", "engine": "pandas", "title": "read_files (SFS helper)",
     "note": "Adds a _source_file column with each row's file name.",
     "code": 'df = read_files("data", "sales_*.csv")\n'},
    {"id": "folder-duckdb", "topic": "read-folder", "engine": "duckdb", "title": "DuckDB glob",
     "code": "SELECT * FROM read_csv_auto('data/sales_*.csv', filename = true)\n"},

    # ── Read from a database ─────────────────────────────────────────────────
    {"id": "db-fast-read", "topic": "read-database", "engine": "pandas", "title": "fast_read (SFS helper)",
     "note": "The same line works in a Polars node, where it returns a Polars DataFrame. "
             "No code at all: a Processor with the Database read engine does the same.",
     "code": 'df = fast_read("my_db", table="sales")\n'},
    {"id": "db-fast-read-polars", "topic": "read-database", "engine": "polars", "title": "fast_read in Polars",
     "code": 'df = fast_read("my_db", sql="SELECT * FROM sales WHERE quantity > 10")\n'},
    {"id": "db-read-sql", "topic": "read-database", "engine": "pandas", "title": "pandas read_sql (for comparison)",
     "note": "Standard pandas, works anywhere; much slower than fast_read for large results.",
     "code": 'df = pd.read_sql("SELECT * FROM sales", get_engine("my_db"))\n'},
    {"id": "db-polars-read-database", "topic": "read-database", "engine": "polars",
     "title": "Polars read_database (for comparison)",
     "code": 'df = pl.read_database("SELECT * FROM sales", get_engine("my_db"))\n'},
    {"id": "db-pushdown", "topic": "read-database", "engine": "sql", "title": "SQL pushdown: let the database do the work",
     "note": "Pick the connection as the node's target. The query runs inside the database and only the "
             "result comes back. DuckDB nodes run SQL over the frames in the flow, not over your "
             "databases: read first (fast_read or Database read), then use DuckDB.",
     "code": "SELECT region, SUM(quantity * unit_price) AS revenue\nFROM sales\nGROUP BY region\n"},

    # ── Write to a database ──────────────────────────────────────────────────
    {"id": "write-pandas", "topic": "write-database", "engine": "pandas", "title": "fast_write (SFS helper)",
     "note": "Uses the database's own bulk loader; far faster than df.to_sql. Creates the table if needed.",
     "needs": ("df",),
     "code": 'fast_write(df, "my_db", "sales_copy", mode="replace")\n'},
    {"id": "write-polars", "topic": "write-database", "engine": "polars", "title": "fast_write from Polars",
     "note": "Accepts a LazyFrame. DuckDB nodes run SQL, so to write a DuckDB result, follow it with "
             "a pandas or Polars node like this one.",
     "needs": ("df",),
     "code": 'fast_write(df, "my_db", "sales_copy", mode="replace")\n'},

    # ── Transform ────────────────────────────────────────────────────────────
    {"id": "clean-pandas", "topic": "transform", "engine": "pandas", "title": "Clean (pandas)", "needs": ("df",),
     "code": 'df = df.dropna(subset=["quantity"])\n'
             'df["region"] = df["region"].str.title()\n'
             'df["revenue"] = df["quantity"] * df["unit_price"]\n'},
    {"id": "summarise-duckdb", "topic": "transform", "engine": "duckdb", "title": "Summarise (DuckDB)",
     "needs": ("df",),
     "code": "SELECT region, SUM(quantity * unit_price) AS revenue\nFROM df\nGROUP BY region\nORDER BY revenue DESC\n"},
    {"id": "summarise-polars", "topic": "transform", "engine": "polars", "title": "Summarise (Polars)",
     "needs": ("df",),
     "code": 'df = (\n    df.group_by("region")\n      .agg((pl.col("quantity") * pl.col("unit_price")).sum().alias("revenue"))\n'
             '      .sort("revenue", descending=True)\n)\n'},

    # ── Several inputs ───────────────────────────────────────────────────────
    {"id": "join-pandas", "topic": "inputs", "engine": "pandas", "title": "Join two inputs (pandas)",
     "note": "Connect two nodes: the first is df, the second df2 (and so on); dfs is all of them. "
             "Only the frame left in df goes downstream.",
     "needs": ("df", "df2"),
     "code": 'df = df.merge(df2, on="product", how="left")\n'},
    {"id": "join-duckdb", "topic": "inputs", "engine": "duckdb", "title": "Join two inputs (DuckDB)",
     "needs": ("df", "df2"),
     "code": "SELECT df.*, df2.category\nFROM df LEFT JOIN df2 USING (product)\n"},
    {"id": "stack-pandas", "topic": "inputs", "engine": "pandas", "title": "Stack every input (pandas)",
     "needs": ("df", "df2"),
     "code": "df = pd.concat(dfs, ignore_index=True)\n"},

    # ── Variables ────────────────────────────────────────────────────────────
    {"id": "vars-pandas", "topic": "vars", "engine": "pandas", "title": "Filter by a variable",
     "note": "Define it in Meta → Variables. Variables are text; convert numbers yourself.",
     "needs": ("df",), "vars": {"region": "North"},
     "code": 'df = df[df["region"] == vars["region"]]\n'},

    # ── Chart ────────────────────────────────────────────────────────────────
    {"id": "chart-matplotlib", "topic": "chart", "engine": "chart", "title": "Chart node: matplotlib",
     "note": "Leave the figure in result. A Plotly figure or an ECharts option dict work too.",
     "needs": ("df",),
     "code": "import matplotlib.pyplot as plt\n\n"
             "totals = df.groupby(\"region\")[\"quantity\"].sum()\n"
             "fig, ax = plt.subplots(figsize=(6, 3))\ntotals.plot(kind=\"bar\", ax=ax)\nax.set_ylabel(\"units\")\n"
             "fig.tight_layout()\nresult = fig\n"},
    {"id": "chart-plotly", "topic": "chart", "engine": "chart", "title": "Chart node: Plotly",
     "needs": ("df",),
     "code": "import plotly.express as px\n\n"
             "result = px.bar(df.groupby(\"region\", as_index=False)[\"quantity\"].sum(), x=\"region\", y=\"quantity\")\n"},
)


def list_examples() -> list[dict]:
    """Examples grouped in topic order, for /api/capabilities."""
    titles = dict(TOPICS)
    order = {t: i for i, (t, _) in enumerate(TOPICS)}
    return [{**e, "topic_title": titles[e["topic"]], "needs": list(e.get("needs", ())),
             "vars": dict(e.get("vars", {}))}
            for e in sorted(EXAMPLES, key=lambda e: order[e["topic"]])]
