# twaky backups

Daily, self-hosted backups of the three Langfuse-v3 backing stores plus the
twaky graph DB. Everything runs on the same host as the stack, over
`twake-network`, without exposing any port.

## What is backed up

| Store        | Container          | What we dump                                        | How                                                    |
|--------------|--------------------|-----------------------------------------------------|--------------------------------------------------------|
| PostgreSQL   | `twaky-pg`         | DB `twaky` (graph + `event_log`) and DB `langfuse`  | `pg_dump -Fc` per DB + `pg_dumpall --globals-only`     |
| ClickHouse   | `twaky-clickhouse` | Every non-system DB, per table: schema + data       | `SHOW CREATE TABLE` + `SELECT ... FORMAT Native \| gz` |
| SeaweedFS S3 | `twaky-seaweedfs`  | Bucket `langfuse` (trace & media blobs)             | `mc mirror` from S3 to a local dir → `tar.gz`          |

Redis (`twaky-redis`) is intentionally **not** backed up: Langfuse uses it as
a queue/cache, so its state is regenerable and losing it costs at most a few
in-flight events. If that's not acceptable in your setup, add a
`docker exec twaky-redis redis-cli SAVE` step; the AOF file is already on the
volume.

## Output layout

```
/home/mmaudet/backups/twaky/
  2026-08-01/
    postgres/
      globals.sql
      twaky.dump
      langfuse.dump
    clickhouse/
      databases.sql
      default.traces.schema.sql
      default.traces.native.gz
      default.observations.schema.sql
      default.observations.native.gz
      ...
    seaweedfs/
      langfuse.tar.gz
    MANIFEST
  2026-07-31/
    ...
```

`MANIFEST` records container image tags, plus size and sha256 for every file
in the day's directory (useful for integrity checks and off-site sync).

## Disk usage estimates

On a fresh, lightly-used deployment (bootstrap only), all four backing stores
combined sit under ~250 MB uncompressed, so **one day's backup is under
~120 MB** after dump compression, and the 14-day rolling window fits in
**~2 GB**. Under real traffic, ClickHouse dominates growth (traces &
observations); rule of thumb:

- If ClickHouse `/var/lib/clickhouse` is *N* GB, expect the Native+gzip dump
  to be roughly *0.3–0.6·N* GB per day.
- Postgres dumps compress ~10× vs. the raw volume for Langfuse metadata.
- SeaweedFS tarball is roughly the sum of stored blobs (Langfuse only
  offloads there when payloads exceed the inline threshold).

Re-check `du -sh /home/mmaudet/backups/twaky/*` weekly for the first month
and adjust `RETENTION_DAYS` if needed.

## Running

### Manual

```bash
# Dry run: prints what would happen, touches nothing.
./scripts/backup.sh --dry-run

# For real.
./scripts/backup.sh

# Restore an entire day.
./scripts/restore.sh 2026-08-01

# Restore only one component.
./scripts/restore.sh 2026-08-01 postgres
./scripts/restore.sh 2026-08-01 clickhouse
./scripts/restore.sh 2026-08-01 seaweedfs

# Non-interactive restore (skips the YES prompt).
FORCE=1 ./scripts/restore.sh 2026-08-01
```

The Makefile wraps the two most common calls:

```bash
make backup
make restore DATE=2026-08-01
```

### Cron (recommended)

Add one line to root's crontab (or the operator's if `docker` is in their
groups):

```
0 3 * * * /home/mmaudet/work/twaky/scripts/backup.sh >> /var/log/twaky-backup.log 2>&1
```

Then:

```bash
sudo touch /var/log/twaky-backup.log
sudo chown mmaudet: /var/log/twaky-backup.log
crontab -e   # paste the line above
```

Verify the next morning: `ls /home/mmaudet/backups/twaky/` should show today's
dated dir; tail the log for any WARN/FATAL lines.

### systemd timer (alternative)

If you prefer systemd over cron, drop these two units in `/etc/systemd/system/`:

`twaky-backup.service`:

```ini
[Unit]
Description=twaky nightly backup (postgres + clickhouse + seaweedfs)
After=docker.service
Requires=docker.service

[Service]
Type=oneshot
User=mmaudet
WorkingDirectory=/home/mmaudet/work/twaky
ExecStart=/home/mmaudet/work/twaky/scripts/backup.sh
StandardOutput=append:/var/log/twaky-backup.log
StandardError=append:/var/log/twaky-backup.log
```

`twaky-backup.timer`:

```ini
[Unit]
Description=Run twaky backup daily at 03:00

[Timer]
OnCalendar=*-*-* 03:00:00
Persistent=true
Unit=twaky-backup.service

[Install]
WantedBy=timers.target
```

Enable:

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now twaky-backup.timer
systemctl list-timers twaky-backup.timer
```

## Restore procedure

The restore script is **destructive**: the target databases and the S3
bucket are wiped and repopulated from the snapshot. Stop the writers first
so they don't observe a half-restored state:

```bash
# 1. Quiesce writers (langfuse ingest, twaky projector/ingest).
docker compose stop twaky-langfuse-web twaky-langfuse-worker \
                    twaky-ingest twaky-projector

# 2. Keep the backing stores up (twaky-pg, twaky-clickhouse, twaky-seaweedfs
#    must be running — the restore talks to them via docker exec / S3).
docker compose ps twaky-pg twaky-clickhouse twaky-seaweedfs

# 3. Restore.
./scripts/restore.sh 2026-08-01
#   → prompts "Type YES to proceed" (set FORCE=1 to skip)

# 4. Bring writers back.
docker compose up -d twaky-langfuse-web twaky-langfuse-worker \
                     twaky-ingest twaky-projector
```

Component-specific notes:

- **Postgres.** The script `DROP DATABASE ... CASCADE`-equivalent (kills open
  connections then drops), recreates the DB owned by the right user, then
  `pg_restore`s the custom-format dump. `globals.sql` is left in place for
  reference only — role passwords in the running cluster are unchanged
  because Langfuse and twaky roles are created by the `.env`-driven
  `docker-entrypoint-initdb.d` scripts on first boot.
- **ClickHouse.** Each user database is dropped and rebuilt from
  `databases.sql`, then every `*.schema.sql` is replayed, then
  `INSERT ... FORMAT Native` streams the data back. Schema migrations
  performed by Langfuse are captured in the `schema_migrations` table, so
  the restored state is exactly the migration head as of the backup date.
- **SeaweedFS.** The bucket is created if needed (`mc mb --ignore-existing`)
  and `mc mirror --overwrite --remove` makes it byte-identical to the
  snapshot (extra objects in the live bucket are deleted).

## Retention & pruning

`backup.sh` prunes date-stamped dirs (`YYYY-MM-DD`) older than 14 days at
the end of each run. Override:

```bash
RETENTION_DAYS=30 ./scripts/backup.sh
```

Off-site replication (recommended once you rely on this): `rclone sync
/home/mmaudet/backups/twaky remote:twaky-backups/` on the same schedule,
after `backup.sh` completes.

## Troubleshooting

- **"container twaky-pg does not exist"** — bring the stack up first
  (`make up`); the backup script does not start containers.
- **`pg_restore: error: could not execute query: ERROR: role "..." does not
  exist`** — the roles are created by initdb from `.env` on first boot of
  `twaky-pg`. If you restore into a freshly-wiped PG volume, start the
  container once (so initdb runs) *then* restore.
- **`mc: <ERROR> Unable to initialize new alias`** — check that `.env` has
  `S3_ACCESS_KEY` / `S3_SECRET_KEY` and that
  `docker network inspect twake-network` shows `twaky-seaweedfs` attached.
- **AGE `LOAD 'age'` needed after restore?** — no. `pg_dump -Fc` captures
  the AGE extension and the graph tables. On restore, `pg_restore` runs
  `CREATE EXTENSION age` before recreating the graph objects.
