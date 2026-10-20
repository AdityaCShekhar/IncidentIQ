# Prior incident: checkout response regression

## Symptoms and resolution
After a checkout revision changed response formatting, checkout returned server errors while inventory calls continued to succeed. Failing requests carried the new revision. Rolling back restored successful checkout responses. The event and matching revision were required to connect the regression to deployment.
