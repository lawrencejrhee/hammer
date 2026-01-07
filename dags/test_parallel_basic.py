"""
Test DAG for basic parallel execution testing.
This DAG has multiple independent tasks that can run in parallel.
"""
from datetime import datetime, timedelta
from airflow import DAG
from airflow.providers.standard.operators.bash import BashOperator
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

with DAG(
    'test_parallel_basic',
    default_args=default_args,
    description='Basic parallel execution test - multiple independent tasks',
    schedule=None,  # Manual trigger only
    start_date=datetime(2024, 1, 1),
    catchup=False,
    tags=['test', 'parallel', 'postgres'],
) as dag:

    def task_function(task_num):
        """Simple task that prints and sleeps"""
        print(f"Task {task_num} starting")
        print(f"Task {task_num} is running with PostgreSQL backend")
        # Sleep for a random time between 2-5 seconds
        sleep_time = random.uniform(2, 5)
        time.sleep(sleep_time)
        print(f"Task {task_num} completed after {sleep_time:.2f} seconds")
        return f"Task {task_num} success"

    # Create 5 independent tasks that can run in parallel
    task1 = PythonOperator(
        task_id='parallel_task_1',
        python_callable=task_function,
        op_args=[1],
    )

    task2 = PythonOperator(
        task_id='parallel_task_2',
        python_callable=task_function,
        op_args=[2],
    )

    task3 = PythonOperator(
        task_id='parallel_task_3',
        python_callable=task_function,
        op_args=[3],
    )

    task4 = PythonOperator(
        task_id='parallel_task_4',
        python_callable=task_function,
        op_args=[4],
    )

    task5 = PythonOperator(
        task_id='parallel_task_5',
        python_callable=task_function,
        op_args=[5],
    )

    # All tasks are independent - no dependencies
    # They will run in parallel if parallelism allows
    [task1, task2, task3, task4, task5]

