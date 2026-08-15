# ecs-service-doctor

**Is your application on ECS actually healthy, or did the control plane just say it is?**

ECS can report a service as stable while your app is still broken: tasks crash-looping after a deploy, load balancer targets failing health checks, the wrong container image running, or the service unable to reach RDS, DynamoDB, or other backends.

This tool checks **applications hosted on AWS ECS** — not just ECS cluster metrics. It validates what matters after a deploy: task counts, **CPU and memory**, rollout state, target group attachment, load balancer health, **the same HTTP health checks as your ALB**, recent ECS events, container images, the connectivity path your app depends on, and **the last few task definitions that ran stably** so you can roll back quickly.

---

## Why applications on ECS?

Many teams run production apps on ECS because it integrates cleanly with the rest of AWS — especially **AI and accelerator workloads** that call **Amazon Bedrock**, store state in **RDS** or **DynamoDB**, pull images from **ECR**, and sit behind **ALB/NLB** with **Route 53** DNS. ECS gives you container orchestration without managing Kubernetes, and it fits naturally into VPC, IAM, and Secrets Manager patterns.

That stack is powerful, but **application health is harder to see than ECS task health**. A service can show `running=2` while:

- Bedrock or database calls fail because env vars, secrets, or security groups are wrong
- The load balancer routes traffic to tasks that fail HTTP health checks
- A rolling deploy leaves two active revisions and half your traffic on a bad build
- Target groups are misconfigured (wrong container port, no registered targets)
- Tasks are up but CPU or memory is saturating and the app is about to fail

ecs-service-doctor answers the question operators and on-call engineers actually care about: **can real traffic reach this app, and is the running task the one we intended to deploy?**

No config file required for a single check.

---

## Features

Everything this repo provides today (**v0.9.1**):

### Application health checks

- **Task counts** — running vs desired vs pending; optional expected desired count per service
- **CPU and memory** — reserved Fargate/EC2 size plus CloudWatch utilization (last 15 minutes); warns at 80%, fails at 90%
- **Deployment status** — rollout finished, in progress, or failed; flags multiple active revisions during deploys
- **HTTP endpoint check** — probes the same path and success codes as the ALB target-group health check (override with config/CLI)
- **Endpoints** — host-header routes and matching Route 53 names are probed with that target-group path/matcher
- **Green / red status lights** — green when the app health check passes, red when it fails
- **Recent ECS events** — latest service messages (placement failures, health check failures, steady state, etc.)
- **Container image** — image URI/tag from the live task definition (what is actually deployed)
- **Pass / warn / fail** — per-check and per-service status with plain-language summaries

### HTML Service Health Report

Shareable, self-contained HTML (no external assets after export):

- **Executive snapshot** — one sentence plus compact fleet counts in the header
- **Needs attention** — only the services that are not healthy, with a jump-to-service link
- **Cluster tabs** — **Services**, **Target groups**, **Load balancers**, and **Route 53** (not repeated on every service)
- **Service picker** — green/red lights, then a short detail panel: capacity, CPU, memory, release, traffic, app, endpoints, known-good versions, events
- **Target groups tab** — every group in the cluster once, with service, port, health-check path/matcher, and healthy/unhealthy counts
- **Load balancers tab** — unique ALB/NLB details once, with the services that use each balancer
- **Route 53 tab** — DNS records that alias or CNAME to cluster ALBs/NLBs, plus names that match listener host headers (CloudFront and CNAME chains included)
- Sample: [`examples/ecs_report.sample.html`](examples/ecs_report.sample.html)

### Continuous monitoring and alerts

- **`--interval 10m`** — re-check on a schedule until you stop the process (also `30s`, `1h`)
- **Slack webhook** — `--notify-slack` or config `notifications.slack_webhook_url`
- **Microsoft Teams** — `--notify-teams` or config `notifications.teams_webhook_url`
- **Generic webhook** — `--notify-webhook` for any JSON endpoint (PagerDuty, custom bots, etc.)
- **SNS** — `--notify-sns` / `notifications.sns_topic_arn` for email/SMS/Lambda fan-out
- **Alert fingerprinting** — same failure is not re-notified on every interval tick until the issue changes or clears
- **Notify on FAIL by default**; add `--notify-on-warn` to include warnings

### Load balancer and target groups

- **Target group detection** — finds target groups attached to the ECS service
- **Attachment validation** — target group exists, is attached to ALB/NLB, ECS container name/port matches the task definition
- **Target health** — healthy, unhealthy, and registering target counts
- **ALB / NLB details** — DNS, scheme, VPC, AZs, listeners, SSL policy, host-header rules with priority numbers
- **Route 53** — hosted-zone records that alias or CNAME to the ALB/NLB (including `dualstack.` targets), plus records whose names match listener host headers
- **Host-header + Route 53 health** — each hostname that belongs to this service is checked using the target-group path and matcher
- **Classic ELB notice** — warns when a service uses a classic load balancer (ALB/NLB required for full TG checks)

### Stable tasks and rollback

- **Last 3 stable task definitions** — recent known-good revisions per service (configurable limit)
- **Discovery sources** — completed deployments, steady-state events, cleanly stopped tasks
- **Rollback commands** — copy-paste `aws ecs update-service` for each stable revision
- **Image per revision** — see which ECR tag belonged to each stable build

### Connectivity auto-detection

Used in the CLI/JSON summary (Route 53, ALB/NLB, target groups, inferred backends):

- **Route 53** — DNS records pointing at the load balancer
- **ALB / NLB** — load balancer name, scheme (public/internal)
- **Cloud Map / Service Connect** — service registry attachments
- **Inferred backends** — RDS, DynamoDB, ElastiCache, DocumentDB, and other AWS endpoints parsed from container env vars and secrets
- **ECR** — container image source

### CLI and configuration

- **Zero config** — `--cluster` + `--service` is enough for a one-off check
- **Multiple services** — repeat `--service` or use `--all-services` for an entire cluster
- **Optional JSON config** — minimal single-cluster config or advanced multi-cluster setup
- **Critical services** — mark services as critical in config; report tracks critical failures separately
- **Account safety** — refuse to run if connected to the wrong AWS account (`--account` or config)
- **Region and profile** — `--region`, `--profile`, or config; region auto-detected from AWS CLI when omitted
- **Parallel checks** — batched and parallel AWS API calls for faster multi-service runs

### Output formats

| Format | Use case |
|--------|----------|
| **Plain CLI** | Default — human-readable HEALTHY / WARNING / UNHEALTHY summary |
| **`--verbose`** | Full technical detail including rollback commands and event lists |
| **`--json`** | Machine-readable report for CI/CD pipelines and automation |
| **`--html`** | Self-contained **ECS Service Health Report** (React, no external assets after export) |

### CI/CD and safety

- **Exit codes** — `0` pass, `1` warnings, `2` failures (script-friendly)
- **Read-only IAM** — no writes to ECS, load balancers, or Route 53; inspect only
- **No agents** — runs from your laptop, CI runner, or CloudShell with AWS credentials

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

# Continuous monitor every 10 minutes + Slack or Teams when health checks fail
python ecs_doctor.py --config config.json --interval 10m \
  --notify-slack https://hooks.slack.com/services/XXX/YYY/ZZZ

python ecs_doctor.py --config config.json --interval 10m \
  --notify-teams https://outlook.office.com/webhook/XXX

# One service with an explicit health URL
python ecs_doctor.py -c my-cluster -s my-api \
  --health-url https://api.example.com/health \
  --interval 10m --notify-webhook https://example.com/hooks/ecs
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
| `--interval 10m` | Continuous checks (`30s`, `10m`, `1h`) |
| `--health-url URL` | HTTP check URL (overrides auto-detect) |
| `--health-path /health` | Override path; otherwise uses the target-group HealthCheckPath |
| `--expected-http-status 200` | Override matcher; otherwise uses the target-group success codes |
| `--notify-slack WEBHOOK` | Slack alert on FAIL |
| `--notify-teams WEBHOOK` | Microsoft Teams alert on FAIL |
| `--notify-webhook URL` | Generic JSON webhook on FAIL |
| `--notify-sns TOPIC_ARN` | SNS publish on FAIL |
| `--notify-on-warn` | Also notify on WARN |

---

## Continuous monitoring and HTTP health alerts

Run a check every 10 minutes and notify when a service is unhealthy or its health endpoint does not match the target-group success codes:

```bash
python ecs_doctor.py --config config.json --interval 10m \
  --notify-slack https://hooks.slack.com/services/XXX/YYY/ZZZ

python ecs_doctor.py --config config.json --interval 10m \
  --notify-teams https://outlook.office.com/webhook/XXX
```

How the HTTP check picks a URL and matcher:

1. Per-service `health_check_url` / `health_check_path` / `expected_http_status` in config
2. CLI `--health-url` / `--health-path` / `--expected-http-status`
3. ALB target-group `HealthCheckPath`, `HealthCheckProtocol`, `Matcher.HttpCode`, and timeout
4. Fallback: `/health` and status `200`

TCP-only target groups are not HTTP-probed. Wildcard host headers are listed but not probed. Route 53 names are included when they match this service’s host-header rules (or all names on the service ALB if there are no host-header rules).

If the response is outside the matcher (for example `200-299`), that service fails and notifications fire.

Example config:

```json
{
  "cluster": "dev-apps-cluster",
  "services": [
    {
      "name": "orders-api",
      "health_check_url": "https://api.example.com/health"
    }
  ],
  "checks": {
    "include_http_health": true,
    "http_expected_status": 200
  },
  "notifications": {
    "on_fail": true,
    "slack_webhook_url": "https://hooks.slack.com/services/XXX/YYY/ZZZ",
    "teams_webhook_url": "https://outlook.office.com/webhook/XXX"
  }
}
```

Alerts include the failing cluster/service, HTTP status/URL when available, and a short issue summary. In continuous mode, the same unhealthy fingerprint is not re-sent every tick until the status changes.

For Microsoft Teams, use an incoming webhook (classic Office connector) or a Power Automate “When a Teams webhook request is received” workflow URL. Both `--notify-teams` and `notifications.teams_webhook_url` work. You can send Slack and Teams at the same time.

---

## What it checks

Application and infrastructure signals together:

| Area | What you learn |
|------|----------------|
| **Tasks** | Running vs desired count — is the app scaled correctly? |
| **CPU / Memory** | Reserved task size (vCPU / GiB) and last-15-minute CloudWatch utilization; warn at 80%, fail at 90% |
| **Deployments** | Rollout finished or stuck with multiple active revisions |
| **Load balancers** | Target groups detected, attached to ALB/NLB, container port matches task definition |
| **Route 53** | Hosted-zone records that alias or CNAME to the service ALB/NLB |
| **Target health** | Healthy vs unhealthy registered targets behind the load balancer |
| **Container image** | Which image/tag is actually deployed (from the task definition) |
| **Recent events** | Latest ECS error messages (task placement failures, health check failures, etc.) |
| **Stable tasks** | Last 3 task definitions that ran stably — with image tag and a copy-paste rollback command |
| **HTTP** | Application URL matches the target-group health-check path and success codes |
| **Endpoints** | Host-header rules and matching Route 53 names get a separate HTTP check per hostname |
| **Connectivity** | Rough path diagram: Route 53 → ALB/NLB → target group → ECS → inferred backends (RDS, DynamoDB, ElastiCache, etc. from env/secrets) → ECR |

This is **read-only** — it inspects your services and produces a CLI summary, JSON for CI/CD, or a shareable HTML **Service Health Report**.

---

## Stable tasks and rollback

When a deploy goes wrong, you often need the **previous task definition revision** — not just a count of running tasks. ECS tracks deployments, but finding “what was last known-good” usually means digging through events or guessing revision numbers.

ecs-service-doctor lists the **last 3 stable task definitions** per service. Each entry includes:

| Field | Description |
|-------|-------------|
| **Task definition** | Short form, e.g. `orders-api:41` — what you pass to `update-service` |
| **Image** | Container image/tag from that revision |
| **Last stable at** | When that revision last reached a stable state |
| **Source** | How it was discovered (see below) |
| **Current** | Whether this is the revision the service runs now |
| **Rollback command** | Ready-to-run AWS CLI command |

### How stable tasks are found

The tool combines three read-only sources:

1. **Completed deployments** — ECS deployment records with `rolloutState: COMPLETED` and running tasks
2. **Steady-state events** — service events like `(service X) has reached a steady state` correlated with task definition revisions
3. **Recently stopped tasks** — tasks stopped cleanly by ECS during deploys or scale-in (`ServiceSchedulerInitiated`, `UserInitiated`), not crash loops

Results are deduplicated by revision, sorted by most recently stable, and limited to **3 by default**.

### Example CLI output

```
[HEALTHY] dev-apps-cluster / orders-api
  Tasks: 2/2 running
  Deployment: finished
  Load balancer: Target groups attached correctly: healthy=2, unhealthy=0
  CPU: 18% average · reserved 0.5 vCPU
  Memory: 41% average · reserved 1 GiB
  Stable task: orders-api:42 (current) — 123456789012.dkr.ecr.us-east-1.amazonaws.com/orders-api:v1.2.3
  Stable task: orders-api:41 — 123456789012.dkr.ecr.us-east-1.amazonaws.com/orders-api:v1.2.2
  Stable task: orders-api:40 — 123456789012.dkr.ecr.us-east-1.amazonaws.com/orders-api:v1.2.1
```

Use `--verbose` to see full rollback commands:

```
  Stable Tasks    : [PASS] 3 recent stable task definition(s): orders-api:42, orders-api:41, orders-api:40
    - orders-api:41 last stable 2026-08-05T14:20:00+00:00 -> .../orders-api:v1.2.2
      Rollback: aws ecs update-service --cluster dev-apps-cluster --service orders-api --task-definition orders-api:41 --force-new-deployment
```

### Roll back to a stable revision

Copy the rollback command from the report or run it yourself:

```bash
aws ecs update-service \
  --cluster dev-apps-cluster \
  --service orders-api \
  --task-definition orders-api:41 \
  --force-new-deployment
```

This does **not** roll back automatically — it only surfaces candidates so you can decide and execute the rollback.

### Config (optional)

In an advanced config file you can tune stable task history:

```json
{
  "checks": {
    "include_stable_task_history": true,
    "stable_task_limit": 3
  }
}
```

Set `include_stable_task_history` to `false` to skip this check. Increase `stable_task_limit` if you want more than 3 rollback candidates (default: `3`).

---

## CPU and memory

Each service reports **reserved** CPU/memory from the task definition and **utilization** from CloudWatch (`AWS/ECS` `CPUUtilization` and `MemoryUtilization`, last 15 minutes).

| Signal | Default |
|--------|---------|
| Warn | ≥ 80% |
| Fail | ≥ 90% |

If CloudWatch has no datapoints yet, reserved size still shows and the service is not failed for missing metrics.

```json
{
  "checks": {
    "include_cpu_memory": true,
    "cpu_warn_percent": 80,
    "cpu_fail_percent": 90,
    "memory_warn_percent": 80,
    "memory_fail_percent": 90
  }
}
```

Set `include_cpu_memory` to `false` to skip this check. Requires `cloudwatch:GetMetricData`.

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
  CPU: 18% average · reserved 0.5 vCPU
  Memory: 41% average · reserved 1 GiB
  Stable task: orders-api:42 (current) — 123456789012.dkr.ecr.us-east-1.amazonaws.com/orders-api:v1.2.3
  Stable task: orders-api:41 — 123456789012.dkr.ecr.us-east-1.amazonaws.com/orders-api:v1.2.2
  Image: 123456789012.dkr.ecr.us-east-1.amazonaws.com/orders-api:v1.2.3
  Connectivity: Route 53 → Load Balancer → Target Group → ECS → backend(s) → ECR

[UNHEALTHY] dev-apps-cluster / payments-api
  Tasks: 1/2 running — Running count is below desired count: running=1, desired=2
  Deployment: Primary deployment rollout state is IN_PROGRESS
  Load balancer: 1 unhealthy target(s) registered
  CPU: 64% average · reserved 1 vCPU
  Memory: 93% average · reserved 2 GiB
  Stable task: payments-api:16 — 123456789012.dkr.ecr.us-east-1.amazonaws.com/payments-api:v2.0.0
  Stable task: payments-api:15 — 123456789012.dkr.ecr.us-east-1.amazonaws.com/payments-api:v1.9.9
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

The HTML report is built for **leadership scan, then drill-down**:

- One-sentence executive snapshot (healthy vs needs attention) and compact fleet counts
- Attention list of only the services that are not healthy
- Per-cluster tabs: **Services**, **Target groups**, **Load balancers**, **Route 53**
- Service details: capacity, CPU, memory, release, traffic, HTTP health, endpoints, known-good versions, events
- Target groups, load balancers, and Route 53 records listed once per cluster

Built with **React + Vite** (`report-ui/`) — glossy `#004170` ops dashboard, **Tahoma** headings and **Calibri** body text.

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
- `ecs:ListTasks`
- `ecs:DescribeTasks`
- `cloudwatch:GetMetricData`
- `elasticloadbalancing:DescribeLoadBalancers`
- `elasticloadbalancing:DescribeListeners`
- `elasticloadbalancing:DescribeRules`
- `elasticloadbalancing:DescribeTargetGroups`
- `elasticloadbalancing:DescribeTargetHealth`
- `route53:ListHostedZones`
- `route53:ListResourceRecordSets`
- `servicediscovery:GetService`
- `sts:GetCallerIdentity`
- `sns:Publish` (only if you use SNS notifications)

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
