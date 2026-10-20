# Retry handling guide

## Detect retry amplification
Group contained client spans by dependency operation and trace. Verify explicit sequential retry attempt attributes and repeated failed attempts. Parallel fan-out without retry attributes is not evidence of retries.

## Safe response
Check retry policy, backoff, and dependency health before changing limits. Preserve request and trace identifiers. Avoid a retry increase while a downstream dependency is already slow or failing.
