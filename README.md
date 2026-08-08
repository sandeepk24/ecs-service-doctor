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

# HTML report — shareable page grouped by cluster
python ecs_doctor.py -c my-cluster --all-services --html
```

**Optional flags**

| Flag | Purpose |
|------|---------|
| `--region us-east-1` | AWS region (auto-detected if omitted) |
| `--profile my-profile` | AWS CLI profile |
| `--account 123456789012` | Refuse to run in the wrong account |
| `--verbose` | Detailed technical output |
| `--html [FILE]` | Write HTML report (default: `ecs_report.html`) |
| `--json` | Machine-readable output for pipelines |

---

## What it checks

- Tasks running vs desired count
- Deployment finished or still rolling out
- Target group detection, attachment validation, and target health
- Container image currently deployed
- Recent ECS error messages
- Connectivity path (Route 53, ALB/NLB, target groups, databases inferred from env vars, ECR)

---

## Config file (optional)

Use a config file when you check many services regularly or need advanced options.

**Minimal config** — copy and edit:

```json
{
  "cluster": "dev-apps-cluster",
  "services": ["orders-api", "payments-api"],
  "region": "us-east-1",
  "expected_account_id": "123456789012"
}
```

`region` and `expected_account_id` are optional. If you omit `expected_account_id`, the tool still runs and shows which account you're connected to. Set it when you want a safety guard (e.g. refuse to run if you're accidentally pointed at prod).

You can also pass account on the CLI without a config file:

```bash
python ecs_doctor.py -c my-cluster -s my-api --account 123456789012
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

## HTML report

Generate a self-contained HTML page you can open in a browser or attach to a release approval:

```bash
python ecs_doctor.py -c my-cluster -s my-api --html
```

Or specify a custom path:

```bash
python ecs_doctor.py -c my-cluster -s my-api --html my-report.html
```

![ECS Service Doctor HTML report sample](examples/ecs_report.sample.png)

Built with **React + Vite** (`report-ui/`) — dark theme, gradient accents, and Plus Jakarta Sans / DM Sans / JetBrains Mono fonts.

The report includes:

- Overall health summary
- Services grouped by cluster
- Task counts, deployment status, load balancer health
- Container images and recent ECS events
- Auto-detected connectivity diagram (Route 53, ALB/NLB, Cloud Map, inferred backends, ECR)

**Preview:** open [`examples/ecs_report.sample.html`](examples/ecs_report.sample.html) in a browser, or see the screenshot above (sample data, no AWS credentials needed).

To regenerate the sample HTML and README screenshot:

```bash
cd report-ui && npm install && npm run build && cd ..
python examples/generate_sample_html.py
```

Requires Playwright for the screenshot step (`cd report-ui && npx playwright install chromium` once).

---

## IAM permissions

Read-only access only:

- `ecs:ListServices`
- `ecs:DescribeServices`
- `ecs:DescribeTaskDefinition`
- `elasticloadbalancing:DescribeLoadBalancers`
- `elasticloadbalancing:DescribeTargetGroups`
- `elasticloadbalancing:DescribeTargetHealth`
- `route53:ListHostedZones`
- `route53:ListResourceRecordSets`
- `servicediscovery:GetService`
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
