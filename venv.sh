source ./.venv/bin/activate

# Set AIRFLOW_HOME to current directory (Hammer root)
export AIRFLOW_HOME=$(pwd)

# Ensure uv and pg_config are on PATH
export PATH="$HOME/pg_local/usr/bin:$HOME/.local/bin:$PATH"

# Source BWRC environment for EDA tools (VCS, Genus, Innovus, etc.)
if [ -f /tools/C/ee290-sp25/bwrc-env.sh ]; then
    source /tools/C/ee290-sp25/bwrc-env.sh
    echo "BWRC EDA environment sourced."
fi

echo "Virtual environment activated."
echo "AIRFLOW_HOME set to: $AIRFLOW_HOME"
