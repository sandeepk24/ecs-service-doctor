# ecs-service-doctor

ECS can tell you a service is stable while your application is still unhealthy.

This tool checks ECS services the way a production engineer actually would:

- Is the desired count equal to the running count?
- Are there pending tasks?
- Is the primary deployment completed?
- Are there multiple active deployments?
- Are target group targets healthy?
- What image is actually running?
- What did ECS recently complain about?

This is not a replacement for CloudWatch, Datadog, New Relic, Dynatrace, or Prometheus.

It is a fast deployment sanity checker for engineers who want to know:

> "Is my ECS service really healthy, or did ECS just say it is?"

---

## Why I built this

I have seen ECS services report stable while the actual application was still not ready behind the load balancer.

In real environments, especially with production deployments, checking desired and running task counts is only the first layer. You also need to look at deployment rollout state, target group health, recent service events, and the task definition that actually went live.

This project is a small utility for that exact gap.

---

## Features

- AWS account safety check (refuses to run if connected to the wrong account)
- ECS service desired / running / pending count validation
- Deployment rollout state checks (PRIMARY, ACTIVE, COMPLETED, IN_PROGRESS, FAILED)
- Multiple active deployment detection
- Recent ECS service event capture
- Task definition + container image inspection
- ALB / NLB target group health validation
- JSON config driven (multi-cluster, multi-service)
- Human-readable and JSON output modes
- CI/CD friendly exit codes

---

## Installation

```bash
git clone https://github.com/sandeepk24/ecs-service-doctor.git
cd ecs-service-doctor
pip install -r requirements.txt
```

You will need AWS credentials available to boto3 (via `~/.aws/credentials`, environment variables, or an IAM role).

Required IAM permissions (read-only):

- `ecs:DescribeServices`
- `ecs:DescribeTaskDefinition`
- `elasticloadbalancing:DescribeTargetHealth`
- `sts:GetCallerIdentity`

---

## Usage

Copy the example config and edit it for your environment:

```bash
cp config.example.json config.json
```

Then run:

```bash
python ecs_doctor.py --config config.json
```

For automation or CI/CD:

```bash
python ecs_doctor.py --config config.json --json
```

---

## Exit codes

| Code | Meaning              |
|------|----------------------|
| 0    | All checks passed    |
| 1    | Warnings found       |
| 2    | Failures found       |
| 130  | Interrupted by user  |

This makes it safe to drop into a deployment pipeline as a post-deploy gate.

---

## Configuration

A minimal config looks like this:

```json
{
  "aws": {
    "region": "us-east-1",
    "profile": "default",
    "expected_account_id": "123456789012"
  },
  "checks": {
    "include_recent_events": true,
    "recent_event_limit": 8,
    "include_target_group_health": true,
    "include_task_definition": true,
    "fail_on_unhealthy_targets": true,
    "warn_on_multiple_active_deployments": true
  },
  "clusters": [
    {
      "name": "dev-apps-cluster",
      "services": [
        { "name": "orders-api",   "expected_desired_count": 2, "critical": true  },
        { "name": "payments-api", "expected_desired_count": 2, "critical": true  }
      ]
    }
  ]
}
```

See `examples/` for additional configurations.

---

## Sample output

```
ECS SERVICE DOCTOR
======================================================================
Generated At : 2026-05-07T15:32:11.482921+00:00
Region       : us-east-1

AWS Account  : [PASS] Connected to AWS account 123456789012

Summary
----------------------------------------------------------------------
Total Services : 3
Passed         : 2
Warnings       : 0
Failed         : 1
----------------------------------------------------------------------
[PASS] dev-apps-cluster / orders-api
  Task Counts     : [PASS] Task counts look stable: desired=2, running=2, pending=0
  Deployments     : [PASS] Deployment state looks stable
  Target Health   : [PASS] Target group health looks good: healthy=2, unhealthy=0
----------------------------------------------------------------------
[FAIL] dev-apps-cluster / payments-api
  Task Counts     : [FAIL] Running count is below desired count: running=1, desired=2
  Deployments     : [FAIL] Primary deployment rollout state is IN_PROGRESS
  Target Health   : [FAIL] Unhealthy targets detected: 1
```

---

## Roadmap

- Markdown report output
- GitHub Actions example
- Slack / Teams webhook notification
- CloudWatch log link generation
- ECS Exec readiness check
- Failed task `stoppedReason` inspection
- Service discovery / Cloud Map check
- HTML report for release approvals
- `--since-deployment` flag (only show events / unhealthy targets after the latest deployment started)

---

## License

MIT
