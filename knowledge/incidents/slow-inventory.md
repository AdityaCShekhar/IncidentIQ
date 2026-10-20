# Prior incident: inventory latency affected checkout

## Symptoms and resolution
Checkout requests slowed while inventory remained available. Correlated traces showed one successful inventory call consuming most of each slow checkout request. The team inspected inventory latency and saturation, then restored the dependency. The evidence supported slow dependency latency, not a checkout database pool failure.
