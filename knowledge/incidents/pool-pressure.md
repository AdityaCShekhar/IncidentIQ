# Prior incident: checkout database pool pressure

## Symptoms and resolution
Checkout requests slowed while waiting acquisitions and utilization gauges from the same named database pool rose together. The team inspected query latency and connection ownership. Unrelated pools and cumulative counters did not support the diagnosis.
