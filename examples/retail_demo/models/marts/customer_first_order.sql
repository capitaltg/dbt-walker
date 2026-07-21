-- reads: customer_id, order_date
select
    customer_id,
    min(order_date) as first_order_date
from {{ ref('stg_orders') }}
group by customer_id
