"""
Test DAG for large-scale parallel execution testing.
This DAG has many tasks (10+) to test system under load.
"""
from datetime import datetime, timedelta
from airflow import DAG
from airflow.providers.standard.operators.python import PythonOperator
import time
import random

default_args = {
    'owner': 'lawrencejrhee',
    'depends_on_past': False,
    'email_on_failure': False,
    'email_on_retry': False,
    'retries': 0,
    'retry_delay': timedelta(minutes=1),
}

def large_parallel_task(task_num):
    """Task for large-scale parallel testing"""
    print(f"Large parallel task {task_num} starting")
    # Simulate work with variable duration
    sleep_time = random.uniform(1, 4)
    time.sleep(sleep_time)
    print(f"Large parallel task {task_num} completed (slept {sleep_time:.2f}s)")
    return f"Task {task_num} done"

with DAG(
    'test_parallel_large',
    default_args=default_args,
    description='Large-scale parallel execution test (10+ tasks)',
    schedule=None,  # Manual trigger only
    start_date=datetime(2024, 1, 1),
    catchup=False,
    tags=['test', 'parallel', 'postgres', 'load-test'],
) as dag:

    # Create 12 independent tasks
    tasks = []
    for i in range(1, 13):
        task = PythonOperator(
            task_id=f'large_task_{i}',
            python_callable=large_parallel_task,
            op_args=[i],
        )
        tasks.append(task)

    # All tasks run in parallel (if parallelism allows)
    tasks

