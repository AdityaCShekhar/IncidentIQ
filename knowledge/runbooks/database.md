# Database pool operations

## Pool saturation
Inspect waiting acquisition and utilization gauges for the same named pool and service. Correlate them with slow or erroring requests in the same time window. Check connection leaks, query latency, and database health before increasing pool size.

## Distinguish cumulative metrics
Counters and cumulative query totals describe accumulated activity. Use incident-window gauges for waiting connections and utilization; do not infer current saturation from cumulative totals.
