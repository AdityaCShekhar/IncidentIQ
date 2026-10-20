# Deployment operations

## Revision regression
Use the deployment event and request revision attributes to connect a release to failing requests. Confirm the service dependency remains healthy, then follow the approved rollback procedure. Verify recovery using requests after the rollback.

## Rollback verification
Do not treat a rollback acknowledgement as proof of recovery. Compare post-rollback request status and dependency spans with the baseline window.
