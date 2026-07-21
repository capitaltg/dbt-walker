-- reads: amount, order_date ; incremental so a schema change may need a full refresh
{{ config(materialized='incremental', unique_key='order_date',
          on_schema_change='append_new_columns') }}
select
    order_date,
    sum(amount) as revenue
from {{ ref('stg_orders') }}
{% if is_incremental() %}
where order_date > (select coalesce(max(order_date), '1900-01-01') from {{ this }})
{% endif %}
group by order_date
