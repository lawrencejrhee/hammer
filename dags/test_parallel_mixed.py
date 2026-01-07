"""
Test DAG for mixed parallel and sequential execution.
This DAG has parallel branches that converge.
"""
from datetime import datetime, timedelta
from airflow import DAG
from airflow.providers.standard.operators.bash import BashOperator
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

with DAG(
    'test_parallel_mixed',
    default_args=default_args,
    description='Mixed parallel/sequential execution test',
    schedule=None,  # Manual trigger only
    start_date=datetime(2024, 1, 1),
    catchup=False,
    tags=['test', 'parallel', 'postgres'],
) as dag:

    # Start task
    start = BashOperator(
        task_id='start',
        bash_command='echo "Starting parallel execution test"',
    )

    # Parallel branch 1: Python tasks
    def python_task_a():
        print("Python task A running")
        time.sleep(3)
        return "Task A complete"

    def python_task_b():
        print("Python task B running")
        time.sleep(3)
        return "Task B complete"

    def python_task_c():
        print("Python task C running")
        time.sleep(3)
        return "Task C complete"

    task_a = PythonOperator(
        task_id='python_task_a',
        python_callable=python_task_a,
    )

    task_b = PythonOperator(
        task_id='python_task_b',
        python_callable=python_task_b,
    )

    task_c = PythonOperator(
        task_id='python_task_c',
        python_callable=python_task_c,
    )

    # Parallel branch 2: Bash tasks
    bash_task_1 = BashOperator(
        task_id='bash_task_1',
        bash_command='echo "Bash task 1" && sleep 3',
    )

    bash_task_2 = BashOperator(
        task_id='bash_task_2',
        bash_command='echo "Bash task 2" && sleep 3',
    )

    bash_task_3 = BashOperator(
        task_id='bash_task_3',
        bash_command='echo "Bash task 3" && sleep 3',
    )

    # End task - runs after all parallel tasks complete
    end = BashOperator(
        task_id='end',
        bash_command='echo "All parallel tasks completed"',
    )

    # Define workflow:
    # start -> [parallel branch 1: task_a, task_b, task_c] -> end
    #       -> [parallel branch 2: bash_task_1, bash_task_2, bash_task_3] -> end
    start >> [task_a, task_b, task_c, bash_task_1, bash_task_2, bash_task_3] >> end

