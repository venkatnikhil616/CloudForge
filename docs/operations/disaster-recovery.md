# Disaster Recovery & Backup Procedures

## PostgreSQL Backup
Execute automated database dump:
```bash
kubectl exec -it postgres-0 -n cloudtask -- pg_dump -U cloudtask cloudtask_db > backup_$(date +%Y%m%d).sql
```

## PostgreSQL Restore
```bash
cat backup_20260904.sql | kubectl exec -i postgres-0 -n cloudtask -- psql -U cloudtask cloudtask_db
```

## Redis Cache Cold Start
If Redis cache is flushed or crashes:
- PostgreSQL holds authoritative durable state for all tasks and schedules.
- System automatically re-populates distributed locks upon subsequent worker execution without data loss.
