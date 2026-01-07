"""
Baseline sequential DAG for comparison with parallel execution.
This DAG runs tasks one after another (sequential).
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

def sequential_task(task_num):
    """Task for sequential execution"""
    print(f"Sequential task {task_num} starting")
    time.sleep(2)  # Fixed 2 second duration
    print(f"Sequential task {task_num} completed")
    return f"Task {task_num} done"

with DAG(
    'test_sequential_baseline',
    default_args=default_args,
    description='Baseline sequential execution test (for comparison)',
    schedule=None,  # Manual trigger only
    start_date=datetime(2024, 1, 1),
    catchup=False,
    tags=['test', 'sequential', 'postgres', 'baseline'],
) as dag:

    # Create 5 tasks that run sequentially
    task1 = PythonOperator(
        task_id='seq_task_1',
        python_callable=sequential_task,
        op_args=[1],
    )

    task2 = PythonOperator(
        task_id='seq_task_2',
        python_callable=sequential_task,
        op_args=[2],
    )

    task3 = PythonOperator(
        task_id='seq_task_3',
        python_callable=sequential_task,
        op_args=[3],
    )

    task4 = PythonOperator(
        task_id='seq_task_4',
        python_callable=sequential_task,
        op_args=[4],
    )

    task5 = PythonOperator(
        task_id='seq_task_5',
        python_callable=sequential_task,
        op_args=[5],
    )

    # Sequential execution: task1 -> task2 -> task3 -> task4 -> task5
    task1 >> task2 >> task3 >> task4 >> task5

