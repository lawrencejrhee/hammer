# Standing up a local Postgres for SledgeHammer

`DATABASE_SETUP.md` assumes you already have a Postgres cluster. This is the
step before that: creating one of your own, in your own directory, with no root
and no help from IT. About 10 minutes.

Use it when the shared database is unreachable and work has to continue, when
you want a private instance for development, or when you are bringing up
SledgeHammer somewhere new.

The cache, the time-saved ledger, and Airflow's own metadata all live in
Postgres, so with the shared server down nothing runs: a DAG trigger is a row
insert, and the scheduler is a loop over rows. A local cluster gets everything
moving again, and the histories can be merged later — the ledger tables are
append-only and the blobs are content-keyed, so a merge is a copy of rows in
either direction, never a reconciliation.

## What you need

- A Postgres server binary. `postgres`, `initdb` and `pg_ctl` come with the
  conda package (`conda install -c conda-forge postgresql`) and need no root.
- A directory on a filesystem with room. Cache blobs dominate: a single
  place-and-route rundir tarball runs to about a gigabyte.
- A free TCP port.

## Setup

### 1. Initialize the cluster

```bash
export DBROOT=/path/to/your/iris-db          # anywhere you can write
mkdir -p $DBROOT
initdb -D $DBROOT/data -U $USER -E UTF8 --locale=C
```

### 2. Configure it

Append to `$DBROOT/data/postgresql.conf`. Pick a port nothing else is using —
check with `ss -ltn | grep <port>` first.

```
listen_addresses = 'localhost'
port = 5544
unix_socket_directories = '/path/to/your/iris-db'
max_connections = 120
```

`listen_addresses = 'localhost'` keeps the cluster off the network, which is
what you want for a personal instance on a shared machine. Putting the socket
in `$DBROOT` rather than `/tmp` avoids collisions with other users' clusters.

### 3. Start it

```bash
pg_ctl -D $DBROOT/data -l $DBROOT/postgres.log start
pg_ctl -D $DBROOT/data status
```

### 4. Set a password and create the databases

```bash
psql -h $DBROOT -p 5544 -U $USER -d postgres \
  -c "ALTER USER $USER PASSWORD 'pick-something';"
psql -h $DBROOT -p 5544 -U $USER -d postgres -c "CREATE DATABASE sledgehammer_studio;"
psql -h $DBROOT -p 5544 -U $USER -d postgres -c "CREATE DATABASE ss_conference;"
```

Two databases, two jobs: `sledgehammer_studio` holds the cache, checkpoints and
ledger; the second holds Airflow's own metadata (the name is arbitrary — it is
referenced only from the connection string below). Keep the password out of
your shell history — write it to a mode-600 file and read it back.

### 5. Point SledgeHammer and Airflow at it

Put this in a file you can source, e.g. `$DBROOT/stack_env.sh`:

```bash
DBROOT=/path/to/your/iris-db
PW=$(cat $DBROOT/.pgpw)

export AIRFLOW_HOME=$DBROOT/airflow
export AIRFLOW__DATABASE__SQL_ALCHEMY_CONN="postgresql+psycopg2://$USER:${PW}@localhost:5544/ss_conference"
export AIRFLOW__CORE__DAGS_FOLDER=/path/to/your/dags
export AIRFLOW__CORE__EXECUTOR=LocalExecutor
export AIRFLOW__CORE__EXECUTION_API_SERVER_URL=http://localhost:8082/execution/

# long EDA stages must not be reaped as zombies
export AIRFLOW__SCHEDULER__TASK_INSTANCE_HEARTBEAT_TIMEOUT=7200
export AIRFLOW__WORKERS__MAX_FAILED_HEARTBEATS=60

# the cache and ledger
export HAMMER_PD_CACHE=1
export HAMMER_PG_HOST=localhost
export HAMMER_PG_PORT=5544
export HAMMER_PG_DB=sledgehammer_studio
export HAMMER_PG_USER=$USER
export HAMMER_PG_PASSWORD=$PW

if [ -f "$HOME/.sledgehammer/smtp.env" ]; then source "$HOME/.sledgehammer/smtp.env"; fi
```

`HAMMER_PD_CACHE=1` is not optional. The cache is off by default, and when it
is off it simply runs every stage — no error, and until recently no log line
either. If a run records nothing in the ledger, check this first.

Everything here is environment only. No config file in any workspace is edited,
so switching back to the shared database later is a matter of not sourcing this
file.

### 6. Create the schemas

```bash
source $DBROOT/stack_env.sh
studio init
airflow db migrate
```

`studio init` builds the hammer tables (`pd_blobs`, `pd_blob_chunks`,
`pd_cache_events`, `pd_checkpoints`, `master_databases`, and the user tables);
`airflow db migrate` builds Airflow's. Verify with `\dt hammer_poc.*` — nine
tables.

### 7. Start Airflow

Four components, each in its own window so a crash is visible:

```bash
tmux new-session -d -s iris-airflow -n api \
  "source $DBROOT/stack_env.sh && exec airflow api-server -H 0.0.0.0 -p 8082 >>$DBROOT/api.log 2>&1"
tmux new-window -t iris-airflow -n sched \
  "source $DBROOT/stack_env.sh && exec airflow scheduler >>$DBROOT/scheduler.log 2>&1"
tmux new-window -t iris-airflow -n dagproc \
  "source $DBROOT/stack_env.sh && exec airflow dag-processor >>$DBROOT/dagproc.log 2>&1"
tmux new-window -t iris-airflow -n trig \
  "source $DBROOT/stack_env.sh && exec airflow triggerer >>$DBROOT/triggerer.log 2>&1"
```

All four are needed. The scheduler dispatches, the dag-processor parses DAG
files into the database, the triggerer services deferred tasks, and in Airflow 3
the api-server also serves the Task Execution API that running tasks report
through — so tasks fail instantly without it, not just the web UI.

### 8. Verify

```bash
source $DBROOT/stack_env.sh
airflow dags list-import-errors      # expect none
airflow dags list                    # your DAGs, from the dags folder
studio cache-status                  # cache + ledger reachable
```

Then trigger something small and confirm a row appears:

```sql
select id, stage, outcome from hammer_poc.pd_cache_events order by id desc limit 5;
```

## Merging back into the shared database

When the shared server returns, the local cluster is the newer of the two.
Dump it into a staging schema first so nothing is overwritten while you look:

```bash
pg_dump -h $DBROOT -p 5544 -U $USER -n hammer_poc sledgehammer_studio > local.sql
```

Load that into a scratch schema on the shared server, then insert across. The
tables make this safe: `pd_cache_events` and `pd_checkpoints` use generated ids
so imported rows never collide; `pd_blobs` and `pd_blob_chunks` are keyed by
content hash, so an identical key means identical bytes. The one table holding
state rather than history is `master_databases` (one row per design,
dependency-check baselines) — newest wins there, so import it last and
deliberately.

## Gotchas

**The port may be taken by something surprising.** An SSH tunnel
(`ssh -L 8082:localhost:8082`) left running will hold the api-server's port on
IPv6 and swallow every task's execution-API connection, which shows up as tasks
failing instantly with zero-byte logs and `RemoteProtocolError: Server
disconnected` in the scheduler log. Check with `ss -ltnp | grep 8082` and look
for `ssh` in the output.

**A guard in your env file can kill the whole stack.** A bare
`[ -f x ] && source x` as the last line returns non-zero when the file is
missing, and with `set -e` in the launcher every component dies at birth. Use
`if [ -f x ]; then source x; fi`.

**Postgres handles a full disk badly** — it stops accepting writes and may need
manual recovery. On a shared filesystem with no quotas, watch `df` and the size
of `pg_wal`.

**Stale run locks block new runs.** Each run claims its build directory; a run
killed before its cleanup task leaves the lock behind, and the next run fails
immediately. The lock file names its owner:

```bash
cat <obj_dir>/.sledgehammer-run.lock
```

Delete it if the owning run is terminal.

## Cost

Metadata is negligible; blobs are everything. A representative instance after
one place-and-route run:

| | |
|---|---|
| cluster total | 2.4 GB |
| `pd_blob_chunks` | 1.1 GB (one par rundir) |
| `pd_checkpoints` | 325 MB |
| everything else | under 1 MB |
| Airflow logs | grows fastest of all — 3.3 GB |

Postgres imposes no cluster size limit, so the ceiling is the filesystem. Prune
with `studio blob-delete` (filter by design, stage or date; `--dry-run` first)
rather than letting blobs accumulate.
