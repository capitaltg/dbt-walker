# retail_demo

A tiny, readable dbt project (DuckDB) for seeing column-aware `impact` prune a
blast radius. One seed, one staging model, four marts — each mart deliberately
reads a *different* subset of `stg_orders` columns:

| mart | reads |
|---|---|
| `revenue_by_customer` (table) | amount, customer_id |
| `daily_revenue` (incremental) | amount, order_date |
| `order_status_counts` (table) | status |
| `customer_first_order` (table) | customer_id, order_date |

## Build it

```
cd examples/retail_demo
..\..\.venv\Scripts\dbt build --profiles-dir .
```

## See the difference a column makes

```
# whole-model change -> all 4 marts (daily_revenue needs a full refresh)
..\..\.venv\Scripts\dbt-walker --project-dir . impact stg_orders

# only `amount` changed -> just the 2 marts that read amount
..\..\.venv\Scripts\dbt-walker --project-dir . impact stg_orders --column amount --dialect duckdb

# only `status` changed -> just order_status_counts, no incremental, no full refresh
..\..\.venv\Scripts\dbt-walker --project-dir . impact stg_orders --column status --dialect duckdb

# trace a single column up or down
..\..\.venv\Scripts\dbt-walker --project-dir . col-upstream   daily_revenue --column revenue --dialect duckdb
..\..\.venv\Scripts\dbt-walker --project-dir . col-downstream stg_orders    --column amount  --dialect duckdb
```
