source ./.venv/bin/activate

# Set AIRFLOW_HOME to current directory (Hammer root)
export AIRFLOW_HOME=$(pwd)

echo "Virtual environment activated."
echo "AIRFLOW_HOME set to: $AIRFLOW_HOME"