import type { EcsReport } from "./types";

export const sampleReport: EcsReport = {
  tool: "ecs-service-doctor",
  version: "0.6.0",
  generated_at: "2026-08-07T20:53:00+00:00",
  region: "us-east-1",
  account_check: {
    status: "PASS",
    message: "Connected to AWS account 123456789012",
    actual_account_id: "123456789012",
  },
  summary: {
    total_services: 3,
    passed: 1,
    warnings: 1,
    failed: 1,
    critical_failed: 1,
  },
  results: [
    {
      cluster: "dev-apps-cluster",
      service: "orders-api",
      critical: true,
      status: "PASS",
      task_definition:
        "arn:aws:ecs:us-east-1:123456789012:task-definition/orders-api:42",
      launch_type: "FARGATE",
      platform_version: "LATEST",
      checks: {
        task_counts: {
          status: "PASS",
          message: "Task counts look stable: desired=2, running=2, pending=0",
        },
        deployments: { status: "PASS", message: "Deployment state looks stable" },
        target_group_health: {
          status: "PASS",
          message: "Target group health looks good: healthy=2, unhealthy=0",
        },
        task_definition: {
          container_images: [
            {
              container: "orders-api",
              image: "123456789012.dkr.ecr.us-east-1.amazonaws.com/orders-api:v1.2.3",
            },
          ],
        },
        recent_events: {
          events: [{ message: "(service orders-api) has reached a steady state." }],
        },
      },
    },
    {
      cluster: "dev-apps-cluster",
      service: "agents-service",
      status: "WARN",
      task_definition:
        "arn:aws:ecs:us-east-1:123456789012:task-definition/agents-service:8",
      launch_type: "FARGATE",
      platform_version: "LATEST",
      checks: {
        task_counts: {
          status: "PASS",
          message: "Task counts look stable: desired=1, running=1, pending=0",
        },
        deployments: {
          status: "WARN",
          message: "Multiple active deployments detected: 2",
        },
        target_group_health: {
          status: "WARN",
          message: "Targets still registering during deployment: initial=1",
        },
        task_definition: {
          container_images: [
            {
              container: "agents-service",
              image:
                "123456789012.dkr.ecr.us-east-1.amazonaws.com/agents-service:v0.9.0",
            },
          ],
        },
        recent_events: {
          events: [
            {
              message:
                "(service agents-service) registered 1 targets in target-group tg-agents",
            },
          ],
        },
      },
    },
    {
      cluster: "dev-apps-cluster",
      service: "payments-api",
      critical: true,
      status: "FAIL",
      task_definition:
        "arn:aws:ecs:us-east-1:123456789012:task-definition/payments-api:17",
      launch_type: "FARGATE",
      platform_version: "LATEST",
      checks: {
        task_counts: {
          status: "FAIL",
          message: "Running count is below desired count: running=1, desired=2",
        },
        deployments: {
          status: "FAIL",
          message: "Primary deployment rollout state is IN_PROGRESS",
        },
        target_group_health: {
          status: "FAIL",
          message: "Unhealthy targets detected: 1",
        },
        task_definition: {
          container_images: [
            {
              container: "payments-api",
              image:
                "123456789012.dkr.ecr.us-east-1.amazonaws.com/payments-api:v2.0.1",
            },
          ],
        },
        recent_events: {
          events: [
            {
              message:
                "(service payments-api) was unable to place a task. Reason: ResourceInitializationError.",
            },
            {
              message:
                "(service payments-api) is unhealthy in target-group tg-payments due to health checks failed.",
            },
          ],
        },
      },
    },
  ],
};
