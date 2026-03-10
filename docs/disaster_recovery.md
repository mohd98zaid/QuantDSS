# QuantDSS Disaster Recovery Plan

## Objective
The objective of this Disaster Recovery (DR) plan is to ensure operational continuity and minimize data loss in the event of a catastrophic failure (e.g., node failure, database corruption, or complete cluster loss).

## Recovery Time Objective (RTO) and Recovery Point Objective (RPO)
*   **RTO**: 15 minutes (time to restore services from a cold standby)
*   **RPO**: 6 hours (based on automated database backup frequency)

## Backup Procedures
*   **Automated PostgreSQL Backups:**
    *   A cronjob schedules `scripts/backup_db.py` every 6 hours.
    *   Backups are exported to the `/backups` network volume (which should be mounted to Amazon S3 or a similar durable storage).
*   **Configuration Backups:**
    *   Kubernetes manifests (`k8s/`) and Prometheus/Grafana configurations are stored under version control.
    *   Secrets must be retrieved from the central Secrets Manager (e.g., AWS Secrets Manager).

## Restore Procedures

### Database Restore
If the database volume is critically corrupted:
1. Bring down all active worker pods to prevent write conflicts:
   ```bash
   kubectl scale deploy -l tier=backend --replicas=0
   ```
2. Re-create the PostgreSQL pod/statefulset.
3. Fetch the latest `quantdss_backup_YYYYMMDD_HHMMSS.sql.gz` from S3.
4. Restore using `pg_restore`:
   ```bash
   pg_restore -h localhost -p 5432 -U postgres -d quantdss -1 quantdss_backup_YYYYMMDD_HHMMSS.sql.gz
   ```
5. Scale the deployments back up:
   ```bash
   kubectl scale deploy -l tier=backend --replicas=1
   ```

### Redis Stream Recovery
Redis streams transient state (like active signals and metrics). If Redis crashes and `noeviction` + AOF (Append Only File) does not recover the state:
1. The `Redis Streams` are ephemeral. The `Recovery Manager` (startup cycle of workers) will resync the `live_trade` table from the PostgreSQL database to reconstruct active memory state.
2. Ensure you run the application `/health` checks.

### Entire Cluster Restore
1. Provision a new Kubernetes cluster via Terraform.
2. Apply the manifest files:
   ```bash
   kubectl apply -f k8s/namespace.yaml
   kubectl apply -k k8s/
   ```
3. Perform Database Restore (above).
4. Restart application pods.
