# Service logs (triage dump)

This folder receives **per-service CloudWatch logs** when you run ecs-service-doctor with `--dump-logs`. Use it offline to triage errors, warnings, and restarts.

## Fetch logs

```bash
# All services in a cluster → ./logs/
python ecs_doctor.py -c my-cluster --all-services --dump-logs

# One or more services
python ecs_doctor.py -c my-cluster -s payments-api -s orders-api --dump-logs

# Custom directory
python ecs_doctor.py -c my-cluster --all-services --dump-logs /tmp/ecs-triage
```

`--dump-logs` raises the CloudWatch fetch limit (default **200** lines per service) so triage has more context than the HTML report.

## Layout after a run

```
logs/
  README.md          ← this file (tracked in git)
  SUMMARY.md         ← severity table for the last dump (gitignored)
  <cluster>/
    <service>.log    ← errors/warnings first, plus restart reasons (gitignored)
```

Each `.log` file includes:

1. Header (cluster, service, status, lookback, summary counts)
2. **Restarts** with exact ECS stop reasons (when present)
3. **Log lines** sorted errors → warnings → routine

Open `SUMMARY.md` first to see which services have errors or warnings, then open that service’s `.log`.

## Notes

- Requires the same AWS credentials and `logs:FilterLogEvents` permission as a normal doctor run.
- Dumped files are **not** committed (see `.gitignore`). Re-run `--dump-logs` whenever you need a fresh triage snapshot.
