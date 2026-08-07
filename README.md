# ecs-service-doctor

**Is your ECS service actually healthy, or did ECS just say it is?**

This tool checks the things that matter after a deploy: running tasks, deployment status, load balancer health, and recent errors. No config file required.

---

## Quick start

**1. Install**

```bash
pip install -r requirements.txt
```

**2. Make sure AWS credentials work** (same as the AWS CLI):

```bash
aws sts get-caller-identity
```

**3. Check a service**

```bash
python ecs_doctor.py --cluster my-cluster --service my-api
```

That's it.

---

## Common commands

```bash
# One service
python ecs_doctor.py -c my-cluster -s my-api

# Several services
python ecs_doctor.py -c my-cluster -s api -s worker -s scheduler

# Every service in a cluster
python ecs_doctor.py -c my-cluster --all-services

# Save checks in a config file (optional)
python ecs_doctor.py --config config.json

# CI/CD — exit code 0 = healthy, 1 = warnings, 2 = failures
python ecs_doctor.py -c my-cluster -s my-api --json
```

**Optional flags**

| Flag | Purpose |
|------|---------|
| `--region us-east-1` | AWS region (auto-detected if omitted) |
| `--profile my-profile` | AWS CLI profile |
| `--account 123456789012` | Refuse to run in the wrong account |
| `--verbose` | Detailed technical output |
| `--json` | Machine-readable output for pipelines |

---

## What it checks

- Tasks running vs desired count
- Deployment finished or still rolling out
- Load balancer target health
- Container image currently deployed
- Recent ECS error messages

---

## Config file (optional)

Use a config file when you check many services regularly or need advanced options.

**Minimal config** — copy and edit:

```json
{
  "cluster": "dev-apps-cluster",
  "services": ["orders-api", "payments-api"]
}
```

**Advanced config** — multiple clusters, expected task counts, account safety:

See [`examples/config.advanced.json`](examples/config.advanced.json).

---

## Sample output

```
ECS Service Check
==================================================
Region: us-east-1
Account: 123456789012

[HEALTHY] dev-apps-cluster / orders-api
  Tasks: 2/2 running
  Deployment: finished
  Load balancer: Target group health looks good: healthy=2, unhealthy=0
  Image: 123456789012.dkr.ecr.us-east-1.amazonaws.com/orders-api:v1.2.3

[UNHEALTHY] dev-apps-cluster / payments-api
  Tasks: 1/2 running — Running count is below desired count: running=1, desired=2
  Deployment: Primary deployment rollout state is IN_PROGRESS
  Load balancer: Unhealthy targets detected: 1
  Latest event: (service payments-api) has started 1 tasks...

==================================================
Result: 1/2 services healthy — problems found
```

---

## IAM permissions

Read-only access only:

- `ecs:ListServices`
- `ecs:DescribeServices`
- `ecs:DescribeTaskDefinition`
- `elasticloadbalancing:DescribeTargetHealth`
- `sts:GetCallerIdentity`

---

## Exit codes

| Code | Meaning |
|------|---------|
| 0 | All checks passed |
| 1 | Warnings (non-critical issues) |
| 2 | Failures found |
| 130 | Interrupted |

---

## License

MIT
