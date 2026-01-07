"""
Test DAG for parallel execution with database operations.
This DAG tests that PostgreSQL can handle concurrent connections from parallel tasks.
Note: In Airflow 3.x, tasks cannot directly access Airflow's metadata database.
This DAG tests parallel execution without database operations for now.
"""
from datetime import datetime, timedelta
from airflow import DAG
from airflow.providers.standard.operators.python import PythonOperator
import time

default_args = {
    'owner': 'lawrencejrhee',
    'depends_on_past': False,
    'email_on_failure': False,
    'email_on_retry': False,
    'retries': 0,
    'retry_delay': timedelta(minutes=1),
}

def database_task(task_id, task_num):
    """Task that simulates database operations"""
    print(f"Task {task_num} ({task_id}) starting simulated database operations")
    
    # Simulate database work without actually accessing the database
    # (Airflow 3.x blocks direct database access from tasks)
    print(f"Task {task_num}: Simulating PostgreSQL connection...")
    time.sleep(1)
    
    print(f"Task {task_num}: Simulating query execution...")
    time.sleep(1)
    
    print(f"Task {task_num}: Simulating data processing...")
    time.sleep(1)
    
    print(f"Task {task_num} ({task_id}) completed simulated database operations successfully")
    return f"Task {task_num} DB operations complete"

with DAG(
    'test_parallel_database',
    default_args=default_args,
    description='Parallel execution test (simulated database operations)',
    schedule=None,  # Manual trigger only
    start_date=datetime(2024, 1, 1),
    catchup=False,
    tags=['test', 'parallel', 'postgres', 'database'],
) as dag:

    # Create 4 tasks that simulate database operations in parallel
    db_task_1 = PythonOperator(
        task_id='db_task_1',
        python_callable=database_task,
        op_args=['db_task_1', 1],
    )

    db_task_2 = PythonOperator(
        task_id='db_task_2',
        python_callable=database_task,
        op_args=['db_task_2', 2],
    )

    db_task_3 = PythonOperator(
        task_id='db_task_3',
        python_callable=database_task,
        op_args=['db_task_3', 3],
    )

    db_task_4 = PythonOperator(
        task_id='db_task_4',
        python_callable=database_task,
        op_args=['db_task_4', 4],
    )

    # All tasks run in parallel
    [db_task_1, db_task_2, db_task_3, db_task_4]
