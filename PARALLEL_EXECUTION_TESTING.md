# Parallel Execution Testing with PostgreSQL

This document describes the test DAGs created for testing parallel execution with PostgreSQL and how to use them.

**Author**: Lawrence Rhee  
**Date**: January 6, 2026

## Overview

This testing suite includes multiple DAGs designed to test Airflow's parallel execution capabilities with PostgreSQL as the metadata database. The DAGs are designed to be tested first with single execution (parallelism=1), then with parallel execution (parallelism>1).

## Current Configuration

**Current Settings (Single Execution Mode):**
- `parallelism = 1` (global limit)
- `max_active_tasks_per_dag = 1` (per-DAG limit)
- `executor = LocalExecutor`

**To enable parallel execution**, update `airflow.cfg`:
```ini
[core]
parallelism = 5  # Allow up to 5 tasks to run concurrently
max_active_tasks_per_dag = 5  # Allow up to 5 tasks per DAG
```

## Test DAGs

### 1. `test_sequential_baseline`
**Purpose**: Baseline for comparison - runs tasks sequentially  
**Tasks**: 5 sequential tasks  
**Execution Time**: ~10 seconds (5 tasks × 2 seconds each)  
**Use Case**: Compare execution time with parallel versions

**Structure**:
```
task1 → task2 → task3 → task4 → task5
```

**To run**:
```bash
airflow dags trigger test_sequential_baseline
```

---

### 2. `test_parallel_basic`
**Purpose**: Basic parallel execution test - multiple independent tasks  
**Tasks**: 5 independent tasks that can run in parallel  
**Execution Time**: 
- Sequential: ~15-25 seconds (tasks sleep 2-5 seconds each)
- Parallel (5 workers): ~5 seconds (all run simultaneously)

**Structure**:
```
[task1, task2, task3, task4, task5]  (all independent, run in parallel)
```

**To run**:
```bash
airflow dags trigger test_parallel_basic
```

**Expected behavior**:
- **Single execution (parallelism=1)**: Tasks run one at a time
- **Parallel execution (parallelism≥5)**: All 5 tasks run simultaneously

---

### 3. `test_parallel_mixed`
**Purpose**: Mixed parallel and sequential execution  
**Tasks**: 6 parallel tasks (3 Python + 3 Bash) that converge to a final task  
**Execution Time**:
- Sequential: ~24 seconds (6 tasks × 3 seconds + 1 final task)
- Parallel (6 workers): ~4 seconds (all run simultaneously, then final task)

**Structure**:
```
start → [task_a, task_b, task_c, bash_1, bash_2, bash_3] → end
```

**To run**:
```bash
airflow dags trigger test_parallel_mixed
```

**Expected behavior**:
- Tests both Python and Bash operators in parallel
- Verifies that tasks can converge after parallel execution

---

### 4. `test_parallel_database`
**Purpose**: Test parallel execution with database operations  
**Tasks**: 4 tasks that all perform PostgreSQL queries concurrently  
**Execution Time**: ~2-3 seconds per task (parallel) vs ~8-12 seconds (sequential)

**Structure**:
```
[db_task_1, db_task_2, db_task_3, db_task_4]  (all perform DB operations)
```

**To run**:
```bash
airflow dags trigger test_parallel_database
```

**What it tests**:
- PostgreSQL connection pooling under concurrent load
- Multiple tasks accessing the database simultaneously
- Database metadata storage during parallel execution

**Expected behavior**:
- Verifies that PostgreSQL can handle concurrent connections
- Tests that task instances are correctly stored in parallel

---

### 5. `test_parallel_large`
**Purpose**: Large-scale parallel execution test  
**Tasks**: 12 independent tasks  
**Execution Time**:
- Sequential: ~18-48 seconds (12 tasks × 1.5-4 seconds each)
- Parallel (12 workers): ~4 seconds (all run simultaneously)

**Structure**:
```
[large_task_1, large_task_2, ..., large_task_12]  (all independent)
```

**To run**:
```bash
airflow dags trigger test_parallel_large
```

**Expected behavior**:
- Tests system under higher load
- Verifies that PostgreSQL can handle many concurrent task instances
- Useful for stress testing

---

## Testing Workflow

### Step 1: Test with Single Execution (Current Setup)

1. **Verify current settings**:
   ```bash
   grep -E "parallelism|max_active_tasks" airflow.cfg
   ```
   Should show:
   ```
   parallelism = 1
   max_active_tasks_per_dag = 1
   ```

2. **Run baseline sequential DAG**:
   ```bash
   airflow dags trigger test_sequential_baseline
   ```
   Note the execution time.

3. **Run parallel DAGs** (they will run sequentially due to parallelism=1):
   ```bash
   airflow dags trigger test_parallel_basic
   airflow dags trigger test_parallel_mixed
   airflow dags trigger test_parallel_database
   ```
   Note: Tasks will run one at a time, but you can verify they work correctly.

4. **Monitor execution**:
   - Check Airflow UI: http://localhost:8090
   - Watch task execution order (should be sequential)
   - Verify all tasks complete successfully
   - Check PostgreSQL metadata storage

### Step 2: Enable Parallel Execution

1. **Update `airflow.cfg`**:
   ```ini
   [core]
   parallelism = 5  # Start with 5, can increase later
   max_active_tasks_per_dag = 5
   ```

2. **Restart Airflow scheduler** (if running):
   ```bash
   # Stop current scheduler
   pkill -f "airflow scheduler"
   
   # Start new scheduler
   source venv.sh
   airflow scheduler
   ```

3. **Run the same DAGs again**:
   ```bash
   airflow dags trigger test_parallel_basic
   airflow dags trigger test_parallel_mixed
   airflow dags trigger test_parallel_database
   ```

4. **Compare results**:
   - Execution time should be significantly faster
   - Multiple tasks should run simultaneously
   - Check Airflow UI to see parallel execution
   - Verify PostgreSQL handles concurrent connections

### Step 3: Scale Up Testing

1. **Increase parallelism**:
   ```ini
   [core]
   parallelism = 10
   max_active_tasks_per_dag = 10
   ```

2. **Run large-scale test**:
   ```bash
   airflow dags trigger test_parallel_large
   ```

3. **Monitor system resources**:
   - Check PostgreSQL connection pool usage
   - Monitor CPU/memory usage
   - Verify no connection errors

## Monitoring Parallel Execution

### Check Running Tasks

```bash
source venv.sh
python3 << 'EOF'
from sqlalchemy import create_engine, text
from airflow.configuration import conf

engine = create_engine(conf.get('database', 'sql_alchemy_conn'))
with engine.connect() as conn:
    result = conn.execute(text("""
        SELECT dag_id, task_id, state, start_date
        FROM task_instance
        WHERE state = 'running'
        ORDER BY start_date
    """))
    
    tasks = list(result)
    if tasks:
        print(f"Currently running {len(tasks)} tasks:")
        for row in tasks:
            print(f"  - {row[0]}.{row[1]} ({row[2]}, started: {row[3]})")
    else:
        print("No tasks currently running")
EOF
```

### Check Task Execution Times

```bash
source venv.sh
python3 << 'EOF'
from sqlalchemy import create_engine, text
from airflow.configuration import conf

engine = create_engine(conf.get('database', 'sql_alchemy_conn'))
with engine.connect() as conn:
    result = conn.execute(text("""
        SELECT 
            dag_id,
            task_id,
            state,
            start_date,
            end_date,
            EXTRACT(EPOCH FROM (end_date - start_date)) as duration_seconds
        FROM task_instance
        WHERE dag_id LIKE 'test_parallel%'
        AND state = 'success'
        AND end_date IS NOT NULL
        ORDER BY start_date DESC
        LIMIT 20
    """))
    
    print("Recent parallel test task execution times:")
    for row in result:
        print(f"  {row[0]}.{row[1]}: {row[5]:.2f}s ({row[2]})")
EOF
```

## Troubleshooting

### DAGs Not Showing Up in UI

**Issue**: New DAGs created but not visible in Airflow UI

**Solutions**:
1. **Force DAG reserialize** (tells scheduler to re-parse all DAGs):
   ```bash
   cd /bwrcq/home/<username>/hammer
   source venv.sh
   airflow dags reserialize
   ```
   Wait 30-60 seconds for the scheduler to parse the DAGs.

2. **Check if DAGs are paused**:
   ```bash
   source venv.sh
   airflow dags list | grep test_
   ```
   If they show as paused, unpause them:
   ```bash
   airflow dags unpause test_parallel_basic
   airflow dags unpause test_parallel_mixed
   # ... etc for each DAG
   ```

3. **Unpause all test DAGs at once**:
   ```bash
   source venv.sh
   for dag in test_parallel_basic test_parallel_mixed test_parallel_database test_parallel_large test_sequential_baseline; do
       airflow dags unpause $dag
   done
   ```

4. **Verify DAGs are in database**:
   ```bash
   source venv.sh
   python3 << 'EOF'
   from sqlalchemy import create_engine, text
   from airflow.configuration import conf
   
   engine = create_engine(conf.get('database', 'sql_alchemy_conn'))
   with engine.connect() as conn:
       result = conn.execute(text("""
           SELECT dag_id, is_paused
           FROM dag
           WHERE dag_id LIKE 'test_%'
           ORDER BY dag_id
       """))
       
       for row in result:
           status = "PAUSED" if row[1] else "ACTIVE"
           print(f"  {row[0]}: {status}")
   EOF
   ```

5. **Check for import errors**:
   ```bash
   source venv.sh
   airflow dags list-import-errors
   ```

6. **Refresh the UI**: Sometimes the UI needs a refresh to show newly parsed DAGs.

### Tasks Not Running in Parallel

**Issue**: Tasks still running sequentially even after increasing parallelism

**Solutions**:
1. Verify `airflow.cfg` changes were saved
2. Restart Airflow scheduler (it reads config on startup)
3. Check that tasks have no dependencies (they should be independent)
4. Verify `max_active_tasks_per_dag` is set correctly

### Database Connection Errors

**Issue**: `psycopg2.OperationalError` or connection pool exhausted

**Solutions**:
1. Increase connection pool size in `airflow.cfg`:
   ```ini
   [database]
   sql_alchemy_pool_size = 20  # Increase from 10
   sql_alchemy_max_overflow = 40  # Increase from 20
   ```
2. Check PostgreSQL max_connections setting
3. Reduce parallelism if pool is too small

### Tasks Stuck in "Queued" State

**Issue**: Tasks queued but not running

**Solutions**:
1. Check if parallelism limit is reached
2. Verify scheduler is running: `ps aux | grep "airflow scheduler"`
3. Check scheduler logs: `tail -f logs/scheduler/latest/*.log`
4. Increase `task_queued_timeout` in `airflow.cfg`

## Next Steps

1. **Document parallel execution setup**: Create guide for configuring parallelism
2. **Cloud Postgres setup**: Begin integration with cloud computing service
3. **Performance benchmarking**: Compare sequential vs parallel execution times
4. **Load testing**: Test with higher parallelism values (10, 20, etc.)

## See Also

- `POSTGRES_SETUP.md` - PostgreSQL setup guide
- `QUALITY_OF_LIFE_COMMANDS.md` - Useful Airflow commands
- `airflow.cfg` - Airflow configuration file

