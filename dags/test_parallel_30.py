"""
Test DAG for large-scale parallel execution testing with 30 tasks.
This DAG has 30 independent tasks that can run in parallel.
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

with DAG(
    'test_parallel_30',
    default_args=default_args,
    description='Large-scale parallel execution test - 30 independent tasks',
    schedule=None,  # Manual trigger only
    start_date=datetime(2024, 1, 1),
    catchup=False,
    tags=['test', 'parallel', 'postgres', 'large-scale'],
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

    # Create 30 independent tasks that can run in parallel
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
    task6 = PythonOperator(
        task_id='parallel_task_6',
        python_callable=task_function,
        op_args=[6],
    )
    task7 = PythonOperator(
        task_id='parallel_task_7',
        python_callable=task_function,
        op_args=[7],
    )
    task8 = PythonOperator(
        task_id='parallel_task_8',
        python_callable=task_function,
        op_args=[8],
    )
    task9 = PythonOperator(
        task_id='parallel_task_9',
        python_callable=task_function,
        op_args=[9],
    )
    task10 = PythonOperator(
        task_id='parallel_task_10',
        python_callable=task_function,
        op_args=[10],
    )
    task11 = PythonOperator(
        task_id='parallel_task_11',
        python_callable=task_function,
        op_args=[11],
    )
    task12 = PythonOperator(
        task_id='parallel_task_12',
        python_callable=task_function,
        op_args=[12],
    )
    task13 = PythonOperator(
        task_id='parallel_task_13',
        python_callable=task_function,
        op_args=[13],
    )
    task14 = PythonOperator(
        task_id='parallel_task_14',
        python_callable=task_function,
        op_args=[14],
    )
    task15 = PythonOperator(
        task_id='parallel_task_15',
        python_callable=task_function,
        op_args=[15],
    )
    task16 = PythonOperator(
        task_id='parallel_task_16',
        python_callable=task_function,
        op_args=[16],
    )
    task17 = PythonOperator(
        task_id='parallel_task_17',
        python_callable=task_function,
        op_args=[17],
    )
    task18 = PythonOperator(
        task_id='parallel_task_18',
        python_callable=task_function,
        op_args=[18],
    )
    task19 = PythonOperator(
        task_id='parallel_task_19',
        python_callable=task_function,
        op_args=[19],
    )
    task20 = PythonOperator(
        task_id='parallel_task_20',
        python_callable=task_function,
        op_args=[20],
    )
    task21 = PythonOperator(
        task_id='parallel_task_21',
        python_callable=task_function,
        op_args=[21],
    )
    task22 = PythonOperator(
        task_id='parallel_task_22',
        python_callable=task_function,
        op_args=[22],
    )
    task23 = PythonOperator(
        task_id='parallel_task_23',
        python_callable=task_function,
        op_args=[23],
    )
    task24 = PythonOperator(
        task_id='parallel_task_24',
        python_callable=task_function,
        op_args=[24],
    )
    task25 = PythonOperator(
        task_id='parallel_task_25',
        python_callable=task_function,
        op_args=[25],
    )
    task26 = PythonOperator(
        task_id='parallel_task_26',
        python_callable=task_function,
        op_args=[26],
    )
    task27 = PythonOperator(
        task_id='parallel_task_27',
        python_callable=task_function,
        op_args=[27],
    )
    task28 = PythonOperator(
        task_id='parallel_task_28',
        python_callable=task_function,
        op_args=[28],
    )
    task29 = PythonOperator(
        task_id='parallel_task_29',
        python_callable=task_function,
        op_args=[29],
    )
    task30 = PythonOperator(
        task_id='parallel_task_30',
        python_callable=task_function,
        op_args=[30],
    )

    # All tasks are independent - no dependencies
    # They will run in parallel if parallelism allows
    [task1, task2, task3, task4, task5, task6, task7, task8, task9, task10,
     task11, task12, task13, task14, task15, task16, task17, task18, task19, task20,
     task21, task22, task23, task24, task25, task26, task27, task28, task29, task30]

