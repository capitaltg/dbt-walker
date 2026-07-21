-- reads: amount, customer_id
select
    customer_id,
    sum(amount) as total_revenue
from {{ ref('stg_orders') }}
group by customer_id
