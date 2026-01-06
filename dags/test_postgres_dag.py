from datetime import datetime, timedelta
from airflow import DAG
# Updated to use provider imports (Airflow 3.x)
from airflow.providers.standard.operators.bash import BashOperator
from airflow.providers.standard.operators.python import PythonOperator

default_args = {
    'owner': 'lawrencejrhee',
    'depends_on_past': False,
    'email_on_failure': False,
    'email_on_retry': False,
    'retries': 1,
    'retry_delay': timedelta(minutes=5),
}

with DAG(
    'test_postgres_dag',
    default_args=default_args,
    description='Test DAG to verify PostgreSQL integration',
    schedule=timedelta(days=1),  # Changed from schedule_interval to schedule
    start_date=datetime(2024, 1, 1),
    catchup=False,
    tags=['test', 'postgres'],
) as dag:

    # Diagnostic smoke test task
    task_smoke = BashOperator(
        task_id='smoke',
        bash_command="""
        set -eux
        whoami
        hostname
        pwd
        env | sort | head -50
        which python || true
        python -V || true
        """,
    )

    # Test with PythonOperator first (more reliable)
    def print_hello():
        print("Hello from Airflow with PostgreSQL!")
        return "Success"

    task1 = PythonOperator(
        task_id='print_hello',
        python_callable=print_hello,
    )

    def print_info():
        print("This task is running with PostgreSQL backend")
        print("Task metadata will be stored in PostgreSQL")
        return "Task completed successfully"

    task2 = PythonOperator(
        task_id='print_info',
        python_callable=print_info,
    )

    task3 = BashOperator(
        task_id='list_files',
        bash_command='ls -la /bwrcq/home/lawrencejrhee/hammer/dags | head -10',
    )

    task_smoke >> task1 >> task2 >> task3