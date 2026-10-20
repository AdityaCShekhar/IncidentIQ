# Prior incident: repeated inventory attempts

## Symptoms and resolution
Slow checkout traces contained sequential attempts for the same inventory operation, including repeated failures. The team checked retry backoff and inventory health. Parallel calls without explicit attempt indices were excluded from the retry diagnosis.
