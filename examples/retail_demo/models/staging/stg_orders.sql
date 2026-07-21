select
    order_id,
    customer_id,
    amount,
    status,
    order_date
from {{ ref('raw_orders') }}
