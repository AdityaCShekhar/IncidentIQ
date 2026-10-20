# Checkout service guide

## Slow dependency requests
When checkout latency rises while inventory calls remain successful, compare checkout server spans with their contained inventory client spans. Check inventory latency and saturation before changing checkout timeouts. A single dependency call that accounts for most of the parent request duration points toward downstream latency.

## Checkout database pool
Inspect waiting acquisitions and pool utilization from the same checkout database pool. Correlate gauge timestamps with slow or failing checkout requests. Cumulative query totals do not establish pool exhaustion.

## Failed checkout after deployment
Compare failing checkout spans with the active revision and deployment event. Confirm that contained inventory calls still succeed. Roll back only through the normal deployment control and verify recovery with new requests.
