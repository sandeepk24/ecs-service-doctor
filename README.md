# ecs-service-doctor

**Is your application on ECS actually healthy, or did the control plane just say it is?**

ECS can report a service as stable while your app is still broken: tasks crash-looping after a deploy, load balancer targets failing health checks, the wrong container image running, or the service unable to reach RDS, DynamoDB, or other backends.

This tool checks **applications hosted on AWS ECS** — not just ECS cluster metrics. It validates what matters after a deploy: task counts, rollout state, target group attachment, load balancer health, recent ECS events, container images, and the connectivity path your app depends on.

---

## Why applications on ECS?

Many teams run production apps on ECS because it integrates cleanly with the rest of AWS — especially **AI and accelerator workloads** that call **Amazon Bedrock**, store state in **RDS** or **DynamoDB**, pull images from **ECR**, and sit behind **ALB/NLB** with **Route 53** DNS. ECS gives you container orchestration without managing Kubernetes, and it fits naturally into VPC, IAM, and Secrets Manager patterns.

That stack is powerful, but **application health is harder to see than ECS task health**. A service can show `running=2` while:

- Bedrock or database calls fail because env vars, secrets, or security groups are wrong
- The load balancer routes traffic to tasks that fail HTTP health checks
- A rolling deploy leaves two active revisions and half your traffic on a bad build
- Target groups are misconfigured (wrong container port, no registered targets)

ecs-service-doctor answers the question operators and on-call engineers actually care about: **can real traffic reach this app, and is the running task the one we intended to deploy?**

No config file required for a single check.

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

Application and infrastructure signals together:

| Area | What you learn |
|------|----------------|
| **Tasks** | Running vs desired count — is the app scaled correctly? |
| **Deployments** | Rollout finished or stuck with multiple active revisions |
| **Load balancers** | Target groups detected, attached to ALB/NLB, container port matches task definition |
| **Target health** | Healthy vs unhealthy registered targets behind the load balancer |
| **Container image** | Which image/tag is actually deployed (from the task definition) |
| **Recent events** | Latest ECS error messages (task placement failures, health check failures, etc.) |
| **Connectivity** | Rough path diagram: Route 53 → ALB/NLB → target group → ECS → inferred backends (RDS, DynamoDB, ElastiCache, etc. from env/secrets) → ECR |

This is **read-only** — it inspects your services and produces a CLI summary, JSON for CI/CD, or a shareable HTML **Service Health Report**.

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
  Load balancer: Target groups attached correctly: healthy=2, unhealthy=0
  Target groups: 1 target group(s): tg-orders — attachments look correct
  Image: 123456789012.dkr.ecr.us-east-1.amazonaws.com/orders-api:v1.2.3
  Connectivity: Route 53 → Load Balancer → Target Group → ECS → backend(s) → ECR

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

Generate a self-contained **ECS Service Health Report** you can open in a browser, attach to a release ticket, or share with your team:

```bash
python ecs_doctor.py -c my-cluster -s my-api --html
```

Or specify a custom path:

```bash
python ecs_doctor.py -c my-cluster -s my-api --html my-report.html
```

![ECS Service Health Report sample](examples/ecs_report.sample.png)

Built with **React + Vite** (`report-ui/`) — dark theme, gradient accents, and Plus Jakarta Sans / DM Sans / JetBrains Mono fonts.

The report includes:

- Overall health summary across clusters
- Services grouped by cluster with pass/warn/fail status
- Task counts, deployment status, and per–target-group attachment details
- Container images and recent ECS events
- Auto-detected connectivity diagram (Route 53, ALB/NLB, target groups, Cloud Map, inferred DB/cache backends, ECR)

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
