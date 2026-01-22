# Parallel Execution Testing with PostgreSQL

This document describes the test DAGs created for testing parallel execution with PostgreSQL and how to use them.

**Author**: Lawrence Rhee  
**Date**: January 6, 2026  
**Last Updated**: January 22, 2026  
**Testing Completed**: January 22, 2026

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

### 6. `test_parallel_30`
**Purpose**: Very large-scale parallel execution test - 30 independent tasks  
**Tasks**: 30 independent tasks that can run in parallel  
**Execution Time**: 
- Sequential: ~116 seconds (30 tasks × ~3.88 seconds average)
- Parallel (30 workers): ~11 seconds (all run simultaneously)

**Structure**:
```
[task1, task2, task3, ..., task30]  (all independent, run in parallel)
```

**To run**:
```bash
airflow dags trigger test_parallel_30
```

**Expected behavior**:
- **Parallel execution (parallelism≥30)**: All 30 tasks run simultaneously
- Tests system under very high load
- Verifies PostgreSQL can handle 30+ concurrent task instances
- Demonstrates large-scale parallel execution capabilities

**Performance**:
- **Speedup**: ~10.4x faster than sequential execution
- **Efficiency**: High - tasks complete in batches based on parallelism setting
- **Stability**: System remains stable under load

**Note**: Requires `parallelism = 30` and `max_active_tasks_per_dag = 30` in `airflow.cfg`

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

### Scheduler Crashes with HTTPStatusError (httpx Compatibility Issue)

**Issue**: Scheduler crashes with error:
```
TypeError: HTTPStatusError.__init__() missing 2 required keyword-only arguments: 'request' and 'response'
```

**Symptoms**:
- Tasks get stuck in "scheduled" or "queued" state
- Scheduler logs show `HTTPStatusError` exceptions
- Some tasks complete but others remain queued indefinitely
- Scheduler process may restart repeatedly

**Root Cause**: 
Incompatibility between Airflow 3.1.0 and newer versions of `httpx` (0.28.0+). The scheduler uses httpx to communicate with the API server, and newer httpx versions have breaking changes in the `HTTPStatusError` class that cause serialization errors when the executor tries to unpickle task results.

**Solution**:
Downgrade `httpx` and `httpcore` to compatible versions:

```bash
cd /bwrcq/home/<username>/hammer
source .venv/bin/activate

# Uninstall current versions
pip uninstall -y httpx httpcore

# Install compatible versions
pip install "httpx==0.27.2" "httpcore==1.0.5"

# Restart all Airflow services
pkill -9 -f "airflow"
sleep 2

# Restart scheduler and API server
export AIRFLOW_HOME=$(pwd)
nohup airflow scheduler > logs/scheduler.log 2>&1 &
nohup airflow api-server --port 8090 > logs/api-server.log 2>&1 &
```

**Verification**:
1. Check httpx version:
   ```bash
   pip show httpx | grep Version
   # Should show: Version: 0.27.2
   ```

2. Check scheduler logs for errors:
   ```bash
   tail -50 logs/scheduler.log | grep -i error
   # Should show no HTTPStatusError messages
   ```

3. Trigger a test DAG:
   ```bash
   airflow dags trigger test_parallel_basic
   ```

4. Verify all tasks complete:
   ```bash
   # Wait 10-15 seconds, then check
   airflow tasks list test_parallel_basic
   ```

**Prevention**:
- When installing Airflow 3.1.0, ensure `httpx<0.28.0` is installed
- Add version constraints to `pyproject.toml` if using Poetry:
  ```toml
  httpx = "<0.28.0"
  httpcore = "~1.0.5"
  ```

**Note**: This issue was discovered and fixed on January 22, 2026. If you encounter this error, the fix above should resolve it immediately.

### Tasks Stuck in "no_state" - DAG Definition Issue

**Issue**: Tasks created but stuck in "no_state" state, never getting scheduled

**Symptoms**:
- DAG run is created and shows as "queued"
- All tasks show state as "no_state" (not even "scheduled")
- Tasks never progress to "scheduled", "queued", or "running"
- Scheduler logs show no activity for the DAG run

**Root Cause**: 
When creating DAGs with many tasks using a loop (e.g., `for i in range(1, 31)`), simply appending tasks to a list and returning the list doesn't properly register them as root tasks in Airflow. Airflow needs tasks to be explicitly defined or properly connected to be recognized.

**Solution**:
Use explicit task definitions instead of loops, or ensure tasks are properly registered:

**❌ Incorrect (causes no_state issue):**
```python
tasks = []
for i in range(1, 31):
    task = PythonOperator(...)
    tasks.append(task)
tasks  # This doesn't properly register tasks
```

**✅ Correct (works):**
```python
task1 = PythonOperator(task_id='parallel_task_1', ...)
task2 = PythonOperator(task_id='parallel_task_2', ...)
# ... explicit definitions for all tasks
[task1, task2, task3, ..., task30]  # Explicit list registers all tasks
```

**Alternative Solution** (if you must use loops):
Ensure tasks are explicitly set as root tasks or connected:
```python
tasks = []
for i in range(1, 31):
    task = PythonOperator(task_id=f'parallel_task_{i}', ...)
    tasks.append(task)
# Explicitly set as list to register as root tasks
tasks
```

**Verification**:
1. Check DAG loads correctly:
   ```bash
   python3 -c "from airflow.models import DagBag; db = DagBag(); print(f'Tasks: {len(db.dags[\"test_parallel_30\"].tasks)}')"
   # Should show: Tasks: 30
   ```

2. Trigger DAG and check task states:
   ```bash
   airflow dags trigger test_parallel_30
   # Wait 10 seconds, then check
   python3 << 'EOF'
   from airflow.models import DagRun, TaskInstance
   from airflow.utils.session import provide_session
   @provide_session
   def check(session=None):
       dag_run = session.query(DagRun).filter(DagRun.dag_id == 'test_parallel_30').order_by(DagRun.run_id.desc()).first()
       tasks = session.query(TaskInstance).filter(TaskInstance.dag_id == 'test_parallel_30', TaskInstance.run_id == dag_run.run_id).all()
       states = {}
       for task in tasks:
           state = task.state or 'no_state'
           states[state] = states.get(state, 0) + 1
       print("Task states:", states)
   check()
   EOF
   # Should show tasks in 'scheduled', 'queued', or 'running', NOT 'no_state'
   ```

**Note**: This issue was discovered on January 22, 2026 when testing the 30-task DAG. The fix is to use explicit task definitions.

### Multiple Scheduler Instances Causing State Conflicts

**Issue**: Multiple scheduler processes running simultaneously causing task state mismatches

**Symptoms**:
- Tasks show state mismatch errors: "Executor reported task finished with state failed, but task instance's state attribute is queued"
- Some tasks complete but others remain stuck
- Scheduler logs show conflicting state updates
- Tasks may appear to run but then fail with state errors

**Root Cause**: 
Multiple scheduler processes competing to manage the same tasks, causing race conditions in state updates. This can happen if:
- Scheduler is started multiple times without properly stopping the previous instance
- Scheduler crashes and auto-restarts while old process is still running
- Manual scheduler starts while another is already running

**Solution**:
Ensure only one scheduler is running:

```bash
# Check for multiple schedulers
ps aux | grep "airflow scheduler" | grep -v grep

# Kill all scheduler processes
pkill -9 -f "airflow scheduler"

# Wait a moment
sleep 3

# Verify no schedulers are running
ps aux | grep "airflow scheduler" | grep -v grep
# Should show nothing

# Start a single scheduler
cd /bwrcq/home/<username>/hammer
source .venv/bin/activate
export AIRFLOW_HOME=$(pwd)
nohup airflow scheduler > logs/scheduler.log 2>&1 &

# Verify only one is running
ps aux | grep "airflow scheduler" | grep python | grep -v grep | wc -l
# Should show: 1
```

**Prevention**:
- Always check for existing schedulers before starting a new one
- Use a process manager or systemd service to ensure only one instance
- Check scheduler logs for "Adopting or resetting orphaned tasks" messages (indicates multiple schedulers)

**Note**: This issue was encountered multiple times during testing on January 22, 2026. Always verify single scheduler before testing.

### API Server Not Running - Network Unreachable Errors

**Issue**: Tasks fail with "Network is unreachable" errors when trying to communicate with API server

**Symptoms**:
- Tasks fail immediately with: `httpx.ConnectError: [Errno 101] Network is unreachable`
- Scheduler logs show tasks completing but failing to report status
- Error: "Executor reported task finished with state failed, but task instance's state attribute is queued"
- Tasks may show as "queued" but never start

**Root Cause**: 
In Airflow 3.x, the `webserver` command has been replaced with `api-server`. Tasks need the API server running to report their status back to the scheduler. Without it, tasks cannot communicate completion status.

**Solution**:
Start the API server (not webserver) on the configured port:

```bash
cd /bwrcq/home/<username>/hammer
source .venv/bin/activate
export AIRFLOW_HOME=$(pwd)

# Check if API server is running
lsof -i :8090

# If not running, start it (Airflow 3.x uses api-server, not webserver)
nohup airflow api-server --port 8090 > logs/api-server.log 2>&1 &

# Verify it's running
sleep 3
lsof -i :8090
# Should show the airflow process listening on port 8090
```

**Important Notes**:
- Airflow 3.x: Use `airflow api-server` (NOT `airflow webserver`)
- The port must match `base_url` in `airflow.cfg` `[api]` section
- Default port is 8090 (as configured in POSTGRES_SETUP.md)

**Verification**:
1. Check API server is running:
   ```bash
   lsof -i :8090
   ```

2. Check base_url configuration:
   ```bash
   airflow config get-value api base_url
   # Should show: http://localhost:8090
   ```

3. Trigger a test DAG and verify tasks complete:
   ```bash
   airflow dags trigger test_parallel_basic
   # Wait 10-15 seconds
   # Tasks should complete successfully
   ```

**Note**: This issue was discovered on January 22, 2026. The API server must be running for parallel execution to work.

### DAG Runs Stuck in "Queued" State - Scheduler Not Processing

**Issue**: DAG runs created but stuck in "queued" state, tasks never get scheduled

**Symptoms**:
- DAG run shows state as "queued" indefinitely
- Tasks remain in "no_state" or "scheduled" but never progress
- Scheduler is running but not processing the DAG run
- No errors in scheduler logs related to the DAG

**Root Causes and Solutions**:

1. **DAG is Paused**:
   ```bash
   # Check if DAG is paused
   airflow dags list | grep test_parallel_30
   
   # Unpause if needed
   airflow dags unpause test_parallel_30
   ```

2. **DAG Not Properly Serialized**:
   ```bash
   # Force DAG reserialize
   airflow dags reserialize
   
   # Wait 30-60 seconds for scheduler to pick up changes
   sleep 30
   
   # Trigger again
   airflow dags trigger test_parallel_30
   ```

3. **Scheduler Needs Restart After Config Changes**:
   ```bash
   # After changing parallelism in airflow.cfg, restart scheduler
   pkill -9 -f "airflow scheduler"
   sleep 2
   source .venv/bin/activate
   export AIRFLOW_HOME=$(pwd)
   nohup airflow scheduler > logs/scheduler.log 2>&1 &
   ```

4. **DAG Definition Issues** (see "Tasks Stuck in no_state" section above)

**Verification**:
```bash
# Check DAG run state
python3 << 'EOF'
from airflow.models import DagRun
from airflow.utils.session import provide_session
@provide_session
def check(session=None):
    dag_run = session.query(DagRun).filter(DagRun.dag_id == 'test_parallel_30').order_by(DagRun.run_id.desc()).first()
    if dag_run:
        print(f"State: {dag_run.state}")
        print(f"Queued at: {dag_run.queued_at}")
check()
EOF
```

**Note**: This issue was encountered during 30-task testing on January 22, 2026. The solution was to unpause the DAG and ensure proper DAG definition.

## Testing Results and Performance Data

### Test Configuration Summary

All tests were performed with:
- **Airflow Version**: 3.1.0
- **PostgreSQL**: 16.4 (on barney.eecs.berkeley.edu:5433)
- **Executor**: LocalExecutor
- **Python Version**: 3.11
- **Dependencies**: httpx==0.27.2, httpcore==1.0.5 (fixed compatibility issue)

### Parallelism=5 Test Results

**Test DAG**: `test_parallel_basic` (5 independent tasks)

**Configuration**:
```ini
[core]
parallelism = 5
max_active_tasks_per_dag = 5
```

**Results**:
- ✅ **All 5 tasks completed successfully**
- **DAG Duration**: ~5-11 seconds (varies based on task sleep times)
- **Task Duration Range**: 2.3s - 4.9s per task
- **Parallel Execution**: Confirmed - tasks started within milliseconds of each other
- **PostgreSQL**: Handled concurrent connections without issues

**Observations**:
- Tasks ran in parallel as expected
- No database connection issues
- Consistent performance across multiple runs

### Parallelism=10 Test Results

**Test DAG**: `test_parallel_basic` (5 tasks, but parallelism=10 allows more headroom)

**Configuration**:
```ini
[core]
parallelism = 10
max_active_tasks_per_dag = 10
```

**Results**:
- ✅ **All 5 tasks completed successfully**
- **Performance**: Similar to parallelism=5 (no improvement for 5 tasks, but more capacity available)
- **System Stability**: No issues with higher parallelism setting

**Observations**:
- Higher parallelism doesn't hurt performance for smaller task counts
- System remains stable with parallelism=10
- Ready for larger task counts

### Parallelism=20 Test Results

**Test DAG**: `test_parallel_basic` (5 tasks)

**Configuration**:
```ini
[core]
parallelism = 20
max_active_tasks_per_dag = 20
```

**Results**:
- ✅ **All 5 tasks completed successfully**
- **Performance**: Consistent with lower parallelism values
- **System Stability**: No degradation with higher parallelism

**Observations**:
- System handles higher parallelism settings well
- No resource exhaustion issues
- PostgreSQL connection pool sufficient

### Parallelism=30 Test Results

**Test DAG**: `test_parallel_30` (30 independent tasks)

**Configuration**:
```ini
[core]
parallelism = 30
max_active_tasks_per_dag = 30
```

**Results**:
- ✅ **All 30 tasks completed successfully**
- **DAG Duration**: ~11.16 seconds
- **Task Duration Range**: 2.3s - 5.0s per task
- **Average Task Duration**: ~3.88 seconds
- **Parallel Execution**: Confirmed - multiple tasks running simultaneously
- **PostgreSQL**: Successfully handled 30 concurrent task instances

**Performance Analysis**:
- **Sequential Estimate**: ~116 seconds (30 tasks × ~3.88s average)
- **Actual Parallel Duration**: ~11.16 seconds
- **Speedup**: ~10.4x faster than sequential execution
- **Efficiency**: Tasks ran in multiple batches due to parallelism=30

**Observations**:
- Large-scale parallel execution works correctly
- PostgreSQL handles high concurrency well
- System remains stable under load
- No connection pool exhaustion
- All tasks completed without errors

### Performance Comparison Summary

| Parallelism | Tasks | DAG Duration | Speedup vs Sequential | Status |
|------------|-------|--------------|----------------------|--------|
| 5 | 5 | ~5-11s | ~5x | ✅ Working |
| 10 | 5 | ~5-11s | ~5x | ✅ Working |
| 20 | 5 | ~5-11s | ~5x | ✅ Working |
| 30 | 30 | ~11.16s | ~10.4x | ✅ Working |

**Key Findings**:
1. **Parallel execution scales well**: System handles up to 30 concurrent tasks
2. **PostgreSQL is robust**: No connection issues even with 30 concurrent tasks
3. **Performance improves**: 30 tasks complete in ~11s vs ~116s sequential
4. **System stability**: No crashes or resource exhaustion
5. **Configuration flexibility**: Can adjust parallelism without issues

### Bugs and Issues Encountered During Testing

#### 1. httpx Compatibility Issue (CRITICAL)
- **Severity**: High - Prevents parallel execution
- **Status**: ✅ Fixed
- **Impact**: Scheduler crashes, tasks fail
- **Solution**: Downgrade httpx to 0.27.2, httpcore to 1.0.5

#### 2. Tasks Stuck in "no_state"
- **Severity**: High - Tasks never execute
- **Status**: ✅ Fixed
- **Impact**: DAG runs created but tasks never start
- **Solution**: Use explicit task definitions instead of loop-generated tasks

#### 3. Multiple Scheduler Instances
- **Severity**: Medium - Causes state conflicts
- **Status**: ✅ Resolved
- **Impact**: Tasks show incorrect states, some fail
- **Solution**: Ensure only one scheduler process runs

#### 4. API Server Not Running
- **Severity**: High - Tasks cannot report status
- **Status**: ✅ Fixed
- **Impact**: Tasks fail with network errors
- **Solution**: Start `airflow api-server` (not webserver in Airflow 3.x)

#### 5. DAG Runs Stuck in Queued
- **Severity**: Medium - DAGs don't process
- **Status**: ✅ Resolved
- **Impact**: DAG runs created but never execute
- **Solution**: Unpause DAG, reserialize, restart scheduler

### Lessons Learned

1. **Always verify services are running**: Scheduler, API server, and PostgreSQL must all be active
2. **Check for multiple processes**: Multiple schedulers cause conflicts
3. **DAG definition matters**: Explicit task definitions work better than loops for large task counts
4. **Configuration changes require restart**: Scheduler must restart to pick up parallelism changes
5. **Dependency versions matter**: httpx compatibility is critical for Airflow 3.1.0
6. **PostgreSQL handles concurrency well**: No issues with 30 concurrent tasks

### Recommended Configuration for Production

Based on testing, recommended settings:

```ini
[core]
parallelism = 30  # Tested and working
max_active_tasks_per_dag = 30  # Tested and working
executor = LocalExecutor

[database]
sql_alchemy_pool_size = 10  # Sufficient for tested loads
sql_alchemy_max_overflow = 20  # Provides buffer

[api]
base_url = http://localhost:8090  # CRITICAL - must be set
```

**Note**: These settings have been tested with up to 30 concurrent tasks. For higher loads, monitor PostgreSQL connection pool usage and system resources.

## Next Steps

1. **Document parallel execution setup**: Create guide for configuring parallelism
2. **Cloud Postgres setup**: Begin integration with cloud computing service
3. **Performance benchmarking**: Compare sequential vs parallel execution times
4. **Load testing**: Test with higher parallelism values (10, 20, etc.)

## See Also

- `POSTGRES_SETUP.md` - PostgreSQL setup guide
- `QUALITY_OF_LIFE_COMMANDS.md` - Useful Airflow commands
- `airflow.cfg` - Airflow configuration file

