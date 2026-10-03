# Budgets

Two AWS Budgets created once by hand in M0, before Terraform existed: `nightshift-monthly-1usd` ($1 a month, email at 100% actual and forecast) and `nightshift-tripwire` ($0.01, email at 100% actual). Both exclude credits and refunds, so credits cannot hide a charge. COST.md explains why.

The notification files hold `<ALERT_EMAIL>` in place of the real address, which stays out of the repository. To create a budget again:

```bash
ACCOUNT_ID=$(aws sts get-caller-identity --query Account --output text)
sed "s/<ALERT_EMAIL>/you@example.com/" tripwire-notifications.json > /tmp/notifications.json
aws budgets create-budget --account-id "$ACCOUNT_ID" \
  --budget file://tripwire.json \
  --notifications-with-subscribers file:///tmp/notifications.json
```
