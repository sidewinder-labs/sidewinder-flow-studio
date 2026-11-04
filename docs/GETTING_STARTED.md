# Getting started

A **flow** is a chain of nodes on the canvas. Each Processor node is one step of
code — Python or SQL — and its output flows along the edge to the next node. You
can click any node after a run and see exactly what came out of it.

The quickest way in: open the project picker, choose **Start from an example →
Sales pipeline (CSV)**, and press **Run Flow**.

## How data moves between nodes

- A node's input arrives as **`df`**. With several inputs, the second is `df2`,
  the third `df3`, and `dfs` is the list of all of them.
- **Only `df` goes downstream.** Whatever the node leaves in `df` when its code
  finishes is its output; other variables you create are gone after the node
  runs.
- In a **DuckDB** node the code is SQL, and the inputs are tables called `df`,
  `df2`, … The query's result is the output.
- In a **Polars** node `df` is a LazyFrame: chain operations and leave the plan
  in `df`, without `.collect()` — SFS runs it, streaming.
- Every output is cached. Click a node to see it in the **Output** panel, and
  re-run from any node without recomputing the ones before it.

## Your first flow, node by node

From CSV to a database table and a chart, in four Processor nodes and an
Explore node. Add nodes from the toolbar, connect them by dragging from one
node's right edge to the next node's left edge, then press **Run Flow**.

**1. Load** (Processor, pandas): read the file.

```python
df = pd.read_csv("data/sales.csv", parse_dates=["date"])
```

**2. Clean** (Processor, pandas): fix what's wrong and add the columns you need.

```python
df = df.dropna(subset=["quantity"])
df["region"] = df["region"].str.title()
df["revenue"] = df["quantity"] * df["unit_price"]
```

**3. Summarise** (Processor, DuckDB): SQL over the cleaned frame.

```sql
SELECT region, SUM(revenue) AS revenue, SUM(quantity) AS units
FROM df
GROUP BY region
ORDER BY revenue DESC
```

**4. Write** (Processor, pandas): load the summary into a database table.
Add the connection first in **Meta → Connections**.

```python
fast_write(df, "my_db", "sales_by_region", mode="replace")
```

`fast_write` uses the database's own bulk loader — far faster than `df.to_sql` —
and creates the table if it doesn't exist. It leaves `df` unchanged, so the
summary still flows on.

**5. Explore** (Explore node, connected after Clean or Summarise): drag fields
onto the axes to chart the data, then **Open in Explore** for the full editor.

Want the same without writing step 1? A **Processor** with the **Database
read** engine reads a table or a query with no code, and **Table View**,
**Chart View** and **Profile** nodes show results without code too.

## Where to go next

- **Functions** (in this help): every helper, such as `fast_read`, `fast_write`
  and `read_files`, with its arguments and an example.
- **Examples**: ready-made code for reading, writing, joining and charting.
  Select a node, then press *Insert into node*.
- **Engines**: when to use pandas, Polars, DuckDB, SQL pushdown or Ibis.
- **Connections**: adding databases, and which ones the desktop app includes.
