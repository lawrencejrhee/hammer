# Quality of Life Commands for Airflow

This document contains useful commands for managing and troubleshooting Airflow.

## Stop All Running DAGs

Sometimes you need to stop all running DAGs and tasks, especially when:
- Too many example DAGs are running
- You want to clear the queue before testing
- You need to free up resources

### Method 1: Using Python Script (Recommended)

Create a script to cancel all running DAG runs and tasks:

```bash
cd /bwrcq/home/<username>/hammer
source venv.sh

python3 << 'EOF'
from sqlalchemy import create_engine, text
from airflow.configuration import conf

engine = create_engine(conf.get('database', 'sql_alchemy_conn'))

with engine.begin() as conn:  # begin() handles commit automatically
    # Cancel all running DAG runs
    result = conn.execute(text("""
        UPDATE dag_run
        SET state = 'failed'
        WHERE state = 'running'
        RETURNING dag_id, run_id
    """))
    
    cancelled_runs = list(result)
    if cancelled_runs:
        print(f"Cancelled {len(cancelled_runs)} running DAG runs:")
        for row in cancelled_runs:
            print(f"  - {row[0]} (run_id: {row[1]})")
    else:
        print("No running DAG runs to cancel")
    
    # Cancel all running task instances
    result2 = conn.execute(text("""
        UPDATE task_instance
        SET state = 'failed'
        WHERE state = 'running'
        RETURNING dag_id, task_id, run_id
    """))
    
    cancelled_tasks = list(result2)
    if cancelled_tasks:
        print(f"\nCancelled {len(cancelled_tasks)} running task instances:")
        for row in cancelled_tasks[:10]:  # Show first 10
            print(f"  - {row[0]}.{row[1]} (run_id: {row[2]})")
        if len(cancelled_tasks) > 10:
            print(f"  ... and {len(cancelled_tasks) - 10} more")
    else:
        print("\nNo running task instances to cancel")
    
    print("\n✅ All running DAGs and tasks have been cancelled")
EOF
```

### Method 2: Check What's Running First

Before cancelling, you can check what's currently running:

```bash
cd /bwrcq/home/<username>/hammer
source venv.sh

python3 << 'EOF'
from sqlalchemy import create_engine, text
from airflow.configuration import conf

engine = create_engine(conf.get('database', 'sql_alchemy_conn'))

with engine.connect() as conn:
    # Get all running DAG runs
    result = conn.execute(text("""
        SELECT dr.dag_id, dr.run_id, dr.state, dr.start_date
        FROM dag_run dr
        WHERE dr.state = 'running'
        ORDER BY dr.start_date DESC
    """))
    
    runs = list(result)
    if runs:
        print(f"Found {len(runs)} running DAG runs:")
        for row in runs:
            print(f"  - {row[0]} (run_id: {row[1]}, started: {row[3]})")
    else:
        print("No running DAG runs found")
    
    # Get all running task instances
    result2 = conn.execute(text("""
        SELECT ti.dag_id, ti.task_id, ti.run_id, ti.state, ti.start_date
        FROM task_instance ti
        WHERE ti.state = 'running'
        ORDER BY ti.start_date DESC
        LIMIT 20
    """))
    
    tasks = list(result2)
    if tasks:
        print(f"\nFound {len(tasks)} running task instances:")
        for row in tasks:
            print(f"  - {row[0]}.{row[1]} (run_id: {row[2]}, started: {row[4]})")
    else:
        print("\nNo running task instances found")
EOF
```

### Method 3: Verify Everything is Stopped

After cancelling, verify that nothing is running:

```bash
cd /bwrcq/home/<username>/hammer
source venv.sh

python3 << 'EOF'
from sqlalchemy import create_engine, text
from airflow.configuration import conf

engine = create_engine(conf.get('database', 'sql_alchemy_conn'))

with engine.connect() as conn:
    result = conn.execute(text("""
        SELECT COUNT(*) as count
        FROM dag_run
        WHERE state = 'running'
    """))
    running_dag_runs = result.fetchone()[0]
    
    result2 = conn.execute(text("""
        SELECT COUNT(*) as count
        FROM task_instance
        WHERE state = 'running'
    """))
    running_tasks = result2.fetchone()[0]
    
    print(f"Verification:")
    print(f"  Running DAG runs: {running_dag_runs}")
    print(f"  Running tasks: {running_tasks}")
    
    if running_dag_runs == 0 and running_tasks == 0:
        print("\n✅ All DAGs and tasks have been stopped")
    else:
        print(f"\n⚠ Still have {running_dag_runs} DAG runs and {running_tasks} tasks running")
EOF
```

## Create a Reusable Script

You can create a reusable script for stopping all DAGs:

**Create `stop_all_dags.sh`:**

```bash
#!/bin/bash
# Stop all running Airflow DAGs and tasks

cd /bwrcq/home/<username>/hammer
source venv.sh

python3 << 'EOF'
from sqlalchemy import create_engine, text
from airflow.configuration import conf

engine = create_engine(conf.get('database', 'sql_alchemy_conn'))

with engine.begin() as conn:
    # Cancel all running DAG runs
    result = conn.execute(text("""
        UPDATE dag_run
        SET state = 'failed'
        WHERE state = 'running'
        RETURNING dag_id, run_id
    """))
    
    cancelled_runs = list(result)
    print(f"Cancelled {len(cancelled_runs)} running DAG runs")
    
    # Cancel all running task instances
    result2 = conn.execute(text("""
        UPDATE task_instance
        SET state = 'failed'
        WHERE state = 'running'
        RETURNING dag_id, task_id, run_id
    """))
    
    cancelled_tasks = list(result2)
    print(f"Cancelled {len(cancelled_tasks)} running task instances")
    
    print("\n✅ All running DAGs and tasks have been cancelled")
EOF
```

**Make it executable:**
```bash
chmod +x stop_all_dags.sh
```

**Usage:**
```bash
./stop_all_dags.sh
```

## Notes

- **What this does**: Marks all running DAG runs and task instances as "failed" in the database
- **Effect**: The scheduler will stop trying to execute these tasks
- **Safety**: This only affects running tasks. Completed, queued, or scheduled tasks are not affected
- **Reversibility**: You cannot "un-cancel" these runs. If you need them, you'll have to trigger new DAG runs

## Pause All DAGs

If you want to prevent DAGs from running (but not cancel current runs), you can pause all DAGs:

### Method 1: Using Python Script (Recommended)

```bash
cd /bwrcq/home/<username>/hammer
source venv.sh

python3 << 'EOF'
from sqlalchemy import create_engine, text
from airflow.configuration import conf

engine = create_engine(conf.get('database', 'sql_alchemy_conn'))

with engine.begin() as conn:
    # Pause all DAGs
    result = conn.execute(text("""
        UPDATE dag
        SET is_paused = true
        WHERE is_paused = false
        RETURNING dag_id
    """))
    
    paused_dags = list(result)
    print(f"Paused {len(paused_dags)} DAGs")
    if paused_dags:
        print("Paused DAGs:")
        for row in paused_dags[:20]:  # Show first 20
            print(f"  - {row[0]}")
        if len(paused_dags) > 20:
            print(f"  ... and {len(paused_dags) - 20} more")
    
    print(f"\n✅ All DAGs are now paused")
EOF
```

### Method 2: Pause Specific DAGs

```bash
cd /bwrcq/home/<username>/hammer
source venv.sh

# Pause a specific DAG
airflow dags pause <dag_id>

# Pause multiple DAGs
for dag_id in dag1 dag2 dag3; do
    airflow dags pause $dag_id
done
```

### Method 3: Check DAG Status

```bash
cd /bwrcq/home/<username>/hammer
source venv.sh

python3 << 'EOF'
from sqlalchemy import create_engine, text
from airflow.configuration import conf

engine = create_engine(conf.get('database', 'sql_alchemy_conn'))
with engine.connect() as conn:
    result = conn.execute(text("""
        SELECT 
            COUNT(*) FILTER (WHERE is_paused = true) as paused_count,
            COUNT(*) FILTER (WHERE is_paused = false) as active_count,
            COUNT(*) as total
        FROM dag
    """))
    
    row = result.fetchone()
    print(f"DAG Status:")
    print(f"  Total DAGs: {row[2]}")
    print(f"  Paused: {row[0]}")
    print(f"  Active: {row[1]}")
EOF
```

## Unpause All DAGs

To unpause all DAGs:

```bash
cd /bwrcq/home/<username>/hammer
source venv.sh

python3 << 'EOF'
from sqlalchemy import create_engine, text
from airflow.configuration import conf

engine = create_engine(conf.get('database', 'sql_alchemy_conn'))

with engine.begin() as conn:
    # Unpause all DAGs
    result = conn.execute(text("""
        UPDATE dag
        SET is_paused = false
        WHERE is_paused = true
        RETURNING dag_id
    """))
    
    unpaused_dags = list(result)
    print(f"Unpaused {len(unpaused_dags)} DAGs")
    print(f"\n✅ All DAGs are now active")
EOF
```

## See Also

- `POSTGRES_SETUP.md` - Main setup guide
- `verify_postgres.sh` - Verify PostgreSQL connection
- `test_postgres_connection.sh` - Test PostgreSQL connection

