# Rollback scripts

Manual rollbacks for the migrations in `../`. There is no alembic in this
codebase: each `NNN_init_*.sh` that needs one has a companion here.

**Rollbacks live in this subdirectory on purpose.** `docker-compose.yml`
mounts `./sql` wholesale as `/docker-entrypoint-initdb.d`, and on a fresh
volume Postgres runs *every* `*.sh` it finds directly in that directory, in
plain alphabetical order. Its entrypoint iterates a flat glob and never
recurses, so anything one level down — this directory — is skipped.

A rollback script sitting next to its migration was therefore executed at
install time. That happened to be harmless as long as every rollback sorted
before its migration (`012_downgrade_…` < `012_init_…`) and used `IF EXISTS`,
but the invariant held by luck alone: a script named `014_rollback_x.sh` sorts
*after* `014_init_x.sh` and would have dropped the freshly created table on
every `make verify-clean`.

## Running one

Rollbacks are always manual, against an existing volume:

```sh
docker exec -e POSTGRES_USER=twaky -i twaky-pg bash < sql/downgrade/012_write_side.sh
```

Read the header of the script first — most require the relevant worker to be
stopped (and its feature flag disabled) so a concurrent tick cannot recreate
rows between the DELETE and the DROP.
