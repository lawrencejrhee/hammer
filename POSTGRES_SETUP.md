# PostgreSQL Setup for SledgeHammer Airflow

This document provides a **complete from-scratch guide** for setting up Apache Airflow with PostgreSQL backend for the SledgeHammer VLSI design framework. This replaces the default SQLite backend to enable concurrent connections, parallel query execution, and multi-user operation.

**Important**: This guide assumes you have **nothing set up** and will walk you through every step, including:
- Python installation (if needed)
- GitHub SSH key setup
- Cloning the Hammer repository
- Installing Poetry and dependencies
- PostgreSQL configuration
- Airflow setup and configuration

## Table of Contents

- [Overview](#overview)
- [Prerequisites](#prerequisites)
- [Complete Setup Process](#complete-setup-process)
  - [Step 0: Install Python (If Not Already Installed)](#step-0-install-python-if-not-already-installed)
  - [Step 0.3: Set Up GitHub SSH Access](#step-03-set-up-github-ssh-access)
  - [Step 0.4: Create Working Directory](#step-04-create-working-directory)
  - [Step 0.5: Clone Hammer Repository](#step-05-clone-hammer-repository)
  - [Step 1: Install Poetry and Dependencies](#step-1-install-poetry-and-dependencies)
    - [Step 1.3.1: Troubleshooting psycopg2 Installation Failure](#step-131-troubleshooting-psycopg2-installation-failure)
  - [Step 1.3.5: Install Apache Airflow](#step-135-install-apache-airflow)
  - [Step 2: Set Up PostgreSQL Connection](#step-2-set-up-postgresql-connection)
  - [Step 3: Configure Airflow](#step-3-configure-airflow)
  - [Step 4: Initialize Database](#step-4-initialize-database)
  - [Step 5: Create Admin User](#step-5-create-admin-user)
  - [Step 6: Start Airflow](#step-6-start-airflow)
- [Configuration Details](#configuration-details)
- [Verification](#verification)
- [Usage](#usage)
- [Troubleshooting](#troubleshooting)
- [Migration from SQLite](#migration-from-sqlite)

## Overview

PostgreSQL provides the foundation for:
- **Concurrent connections**: Multiple Airflow tasks and users can access the database simultaneously
- **Parallel execution**: Airflow can run multiple tasks in parallel without database locking issues
- **Scalability**: Support for large-scale VLSI design flows with many concurrent workflows
- **Multi-user collaboration**: Multiple users can run design flows on shared servers

## Prerequisites

1. **SSH Access to BWRC Machines**: 
   - **Login Server**: `bwrcrdsl-1.eecs.berkeley.edu` (for initial connection)
   - **Compute Node**: You should be on a BWRC compute node (e.g., `bwrcix-3`, `bwrcix-4`, etc.), not the login server
   ```bash
   # First, SSH to login server
   ssh <username>@bwrcrdsl-1.eecs.berkeley.edu
   
   # Then SSH to any available compute node (bwrcix-3 is just an example)
   ssh <username>@bwrcix-3.eecs.berkeley.edu
   # Or try other compute nodes if bwrcix-3 is unavailable:
   # ssh <username>@bwrcix-4.eecs.berkeley.edu
   ```
   
   **Note**: Airflow should run on compute nodes, not the login server, to avoid resource conflicts. Any available compute node will work - `bwrcix-3` is just an example.

2. **GitHub Account**: You need a GitHub account with SSH access configured (see Step 0.3 below).

3. **Working Directory**: You will clone the Hammer repository to your working directory (see Step 0.5 below).

4. **PostgreSQL Server**: Access to a PostgreSQL server (currently configured for `barney.eecs.berkeley.edu:5433`)
   
   **If the PostgreSQL server is down**, contact:
   - **Email**: anne_young@berkeley.edu
   - **System Admins**: bwrc-sysadmins@lists.eecs.berkeley.edu

5. **Database**: A PostgreSQL database must exist (e.g., `airflow_<username>`)

6. **Python 3.10-3.13**: Required for Airflow 3.1.0

7. **Poetry**: Python dependency management tool

## Complete Setup Process

### Step 0: Install Python (If Not Already Installed)

If Python 3.10-3.13 is not already available on your system, you need to build it from source.

1. **Download Python 3.11.9**:
   ```bash
   cd ~
   wget https://www.python.org/ftp/python/3.11.9/Python-3.11.9.tgz
   ```

2. **Extract and Build**:
   ```bash
   tar -xzf Python-3.11.9.tgz
   cd Python-3.11.9
   
   # Configure and build (this may take 10-30 minutes)
   ./configure --prefix=$HOME/python311 --enable-optimizations --with-ensurepip=yes
   make -j$(nproc)
   make install
   
   # Clean up source files (optional, saves ~380MB)
   cd ~
   rm -rf Python-3.11.9 Python-3.11.9.tgz
   ```

3. **Add Python to PATH**:
   ```bash
   echo 'export PATH="$HOME/python311/bin:$PATH"' >> ~/.bashrc
   source ~/.bashrc
   ```

4. **Verify Installation**:
   ```bash
   python3 --version
   # Should show: Python 3.11.9
   ```

**Note**: On BWRC servers, if you don't have root access to install system packages, you may need to build Python from source as shown above. If you need system-level Python installation or root access, contact:
- **Email**: anne_young@berkeley.edu
- **System Admins**: bwrc-sysadmins@lists.eecs.berkeley.edu

### Step 0.3: Set Up GitHub SSH Access

**CRITICAL**: You must set up SSH access to GitHub before you can clone the Hammer repository. This step is required and must be completed on the BWRC server where you will be working.

1. **Generate SSH Key** (if you don't already have one):
   ```bash
   ssh-keygen -t ed25519 -C "your_email@berkeley.edu"
   ```
   
   - Press Enter to accept the default file location (`~/.ssh/id_ed25519`)
   - Enter a passphrase (optional but recommended) or press Enter twice to skip
   
   **Note**: If you already have an SSH key, you can skip this step or generate a new one with a different name.

2. **Start SSH Agent and Add Key**:
   ```bash
   eval "$(ssh-agent -s)"
   ssh-add ~/.ssh/id_ed25519
   ```
   
   If you used a different key name or location, adjust the path accordingly.

3. **Copy Your Public Key**:
   ```bash
   cat ~/.ssh/id_ed25519.pub
   ```
   
   This will display your public key. **Copy the entire output** (it should start with `ssh-ed25519` and end with your email).

4. **Add SSH Key to GitHub**:
   - Go to GitHub.com and log in
   - Click your profile picture → **Settings**
   - In the left sidebar, click **SSH and GPG keys**
   - Click **New SSH key**
   - Give it a title (e.g., "BWRC Server")
   - Paste your public key into the "Key" field
   - Click **Add SSH key**

5. **Verify SSH Connection to GitHub**:
   ```bash
   ssh -T git@github.com
   ```
   
   You should see a message like:
   ```
   Hi <username>! You've successfully authenticated, but GitHub does not provide shell access.
   ```
   
   If you see a permission denied error, check that:
   - Your public key was correctly copied to GitHub
   - The SSH agent is running and has your key loaded
   - You're using the correct key file

**Troubleshooting**:
- If `ssh-keygen` is not found, you may need to install OpenSSH
- If authentication fails, verify your public key is correctly added to GitHub
- For more detailed instructions, see [GitHub's SSH Key Guide](https://docs.github.com/en/authentication/connecting-to-github-with-ssh)

### Step 0.4: Create Working Directory

Before cloning the repository, create your working directory:

```bash
mkdir -p /bwrcq/C/<username>
cd /bwrcq/C/<username>
```

**Note**: Replace `<username>` with your actual LDAP username. This directory will hold the Hammer repository and all your work files.

### Step 0.5: Clone Hammer Repository

**IMPORTANT**: You must clone the Hammer repository before proceeding with Poetry installation. The `pyproject.toml` file in the repository contains the correct dependencies needed for Airflow and PostgreSQL.

**Prerequisites**: Make sure you have completed:
- Step 0.3: Set Up GitHub SSH Access (required for cloning)
- Step 0.4: Create Working Directory

1. **Navigate to your working directory** (if not already there):
   ```bash
   cd /bwrcq/C/<username>
   ```

2. **Clone the Hammer repository**:
   ```bash
   git clone git@github.com:ucb-bar/hammer.git
   cd hammer
   ```

   **Optional**: If you want to clone into a different directory name:
   ```bash
   git clone git@github.com:ucb-bar/hammer.git <directory name>
   cd <directory name>
   ```

3. **Verify you're in the Hammer directory**:
   ```bash
   pwd
   # Should show: /bwrcq/C/<username>/hammer
   ls -la
   # Should show pyproject.toml, poetry.lock, and other Hammer files
   ```

**Troubleshooting**:
- If you get "Permission denied (publickey)" when cloning, go back to Step 0.3 and verify your SSH key is set up correctly
- If you get "Host key verification failed", run: `ssh-keyscan github.com >> ~/.ssh/known_hosts`
- If you prefer HTTPS instead of SSH, you can use: `git clone https://github.com/ucb-bar/hammer.git` (but SSH is recommended)

### Step 1: Install Poetry and Dependencies

**CRITICAL**: Before installing dependencies, ensure your `pyproject.toml` file matches the one in the Hammer repository. The `pyproject.toml` **must** include the PostgreSQL-related dependencies.

**Why This Is Critical**:
- `psycopg2` is the PostgreSQL database adapter that Airflow uses to connect to PostgreSQL. Without it, Airflow cannot connect to the PostgreSQL database.
- `asyncpg` is required by Airflow 3.1.0 for async database operations. Missing this will cause import errors.
- `pydantic` version must be >=2.12.2 for Airflow 3.1.0 compatibility. Older versions will cause runtime errors.

**Full `pyproject.toml` Reference**:

Your `pyproject.toml` should match the following (copy-paste this if you need to recreate it):

```toml
[tool.poetry]
name = "hammer-vlsi"
version = "0.0.0"
description = "Hammer is a physical design framework that wraps around vendor specific technologies and tools to provide a single API to create ASICs."
authors = ["Colin Schmidt <colin.schmidt@sifive.com>", "Edward Wang <edward.c.wang@compdigitec.com>", "John Wright <johnwright@eecs.berkeley.edu>"]
maintainers = ["Harrison Liew <harrisonliew@berkeley.edu>", "Daniel Grubb <dpgrubb@berkeley.edu>"]
readme = "README.md"
repository = "https://github.com/ucb-bar/hammer"
packages = [
    {include = "hammer"}
]

[tool.poetry.scripts]
hammer-generate-mdf = "hammer.shell.hammer_generate_mdf:main"
get-config = "hammer.shell.get_config:main"
hammer-vlsi = "hammer.shell.hammer_vlsi:main"
hammer-shell-test = "hammer.shell.hammer_shell_test:main"
readlink-array = "hammer.shell.readlink_array:main"
yaml2json = "hammer.shell.yaml2json:main"
asap7_gds_scale = "hammer.technology.asap7.gds_scale:main"

[tool.poetry.dependencies]
python = ">=3.10,<3.14"
PyYAML = "^6.0"
"ruamel.yaml" = "^0.17.21"
networkx = "^3.0"
numpy = "^1.23.0"
sphinxcontrib-mermaid = "^0.8.1"
pydantic = "^2.12.2"
psycopg2 = "^2.9.11"
asyncpg = "^0.30.0"

gdstk = { version = "^0.9.0", optional = true }
gdspy = { version = "1.4", optional = true }

[tool.poetry.extras]
asap7-gdstk = ["gdstk"]
asap7 = ["gdspy"]

[tool.poetry.dev-dependencies]
pytest = "^7.1"
pyright = "^1.1"
types-PyYAML = "^6.0.0"
tox = "^3.25.1"
Sphinx = "^5.1.1"
sphinx-rtd-theme = "^1.0.0"
myst-parser = "^0.18.0"
sphinx-jsonschema = "^1.19.1"
sphinx_rtd_size = "^0.2.0"
pylint = "^2.15"

[[tool.poetry.source]]
name = "testpypi"
url = "https://test.pypi.org/legacy/"
priority = "explicit"

[build-system]
requires = ["poetry-core>=1.0.8", "setuptools>=65.3"]
build-backend = "poetry.core.masonry.api"

[tool.tox]
legacy_tox_ini = """
[tox]
envlist = py39,py310
isolated_build = True

[testenv]
deps = pytest
commands =
  pytest tests/ {posargs}
"""

[tool.pylint.format]
# Maximum number of characters on a single line.
max-line-length = 120

[tool.pylint.reports]
output-format = "text"
reports = true

[tool.pylint."messages control"]

# Disable no-else-return since it's sometimes clearer to use else
# Also emulates a functional programming style
# Disable len-as-condition since len(lst) == 0 is more clear on types
# where as if <var> is confusing since it's not clear what <var> is.
disable = ["no-else-return", "len-as-condition"]

[tool.pyright]
exclude = ["doc/**", "e2e/**", "test_scripts/**"]
include = ["hammer", "tests"]
reportMissingImports = true
typeCheckingMode = "standard"
```

**Key Dependencies for PostgreSQL**:
- `psycopg2 = "^2.9.11"` - PostgreSQL adapter (REQUIRED for PostgreSQL backend)
- `asyncpg = "^0.30.0"` - Required by Airflow 3.1.0 (REQUIRED)
- `pydantic = "^2.12.2"` - Required by Airflow 3.1.0 (REQUIRED)

**What To Do**:
1. **Verify** your `pyproject.toml` matches the above (especially the `[tool.poetry.dependencies]` section)
2. If your file is missing any of the PostgreSQL dependencies, **copy the full file above** or add the missing dependencies
3. If you modified `pyproject.toml`, run `poetry lock --no-update` to update `poetry.lock` without upgrading other packages
4. **Do not proceed** with `poetry install` until these dependencies are confirmed in `pyproject.toml`

**Note**: The `pyproject.toml` in the Hammer repository already includes these dependencies. If you're working with a fresh clone, your `pyproject.toml` should already be correct. If you're working with an existing setup, verify these dependencies are present or copy the full file above.

#### 1.1 Install Poetry

```bash
# CRITICAL: Use the explicit Python 3.11.9 path to ensure Poetry is installed correctly
# This ensures Poetry uses Python 3.11.9 instead of the system Python
curl -sSL https://install.python-poetry.org | $HOME/python311/bin/python3 -

# Add Poetry to PATH (add to ~/.bashrc for persistence)
export PATH="$HOME/.local/bin:$PATH"

# Verify installation
poetry --version
```

#### 1.2 Configure Poetry

```bash
# Make sure you're in the Hammer directory (from Step 0.5)
cd /bwrcq/C/<username>/hammer

# Configure Poetry to create virtual environment in project directory
poetry config virtualenvs.in-project true

# CRITICAL: Explicitly tell Poetry to use Python 3.11.9 for the virtual environment
# This ensures the venv is created with the correct Python version
poetry env use $HOME/python311/bin/python3

# Verify Poetry will use the correct Python
poetry env info
# Should show Python 3.11.9 in the output
```

#### 1.3 Install Dependencies from poetry.lock

**IMPORTANT**: Before running `poetry install`, verify that Poetry will use Python 3.11.9:

```bash
# Verify Python version that Poetry will use
poetry run python --version
# Should show: Python 3.11.9

# If it shows a different version, run this again:
poetry env use $HOME/python311/bin/python3
poetry run python --version  # Verify again
```

Now install dependencies:

```bash
# Install all dependencies (Airflow will be installed separately in Step 1.3.5)
poetry install

# This will:
# - Create .venv directory in the project with Python 3.11.9
# - Install all dependencies from poetry.lock
# - Install required packages:
#   - psycopg2 (PostgreSQL adapter)
#   - asyncpg (required by Airflow)
#   - pydantic (>=2.12.2)
# 
# NOTE: Apache Airflow is NOT installed by poetry install.
# You must install it separately in Step 1.3.5 below.
```

**After installation, verify the venv was created with the correct Python:**

```bash
# Check Python version in the venv
.venv/bin/python --version
# Should show: Python 3.11.9
```

#### 1.3.1 Troubleshooting: psycopg2 Installation Failure

If `poetry install` fails with an error like:

```
Error: pg_config executable not found.
pg_config is required to build psycopg2 from source.
```

This means Poetry is trying to build `psycopg2` from source instead of using a pre-built wheel. **Do NOT switch to `psycopg2-binary`** - instead, manually install `psycopg2` using pip:

```bash
# First, activate the virtual environment
source .venv/bin/activate

# Install psycopg2 directly with pip (this uses cached wheels if available)
pip install psycopg2==2.9.11

# Verify it installed correctly
python -c "import psycopg2; print('psycopg2 version:', psycopg2.__version__)"
# Should show: psycopg2 version: 2.9.11 (dt dec pq3 ext lo64)

# Now run poetry install again to install remaining dependencies
poetry install
```

**Why this works**: `pip install psycopg2==2.9.11` will use cached wheel files (`.whl`) if they exist on your system, avoiding the need to compile from source. The BWRC servers typically have these wheels cached.

**If pip also fails to install psycopg2**, contact system administrators to install PostgreSQL development libraries:
- **Email**: anne_young@berkeley.edu
- **System Admins**: bwrc-sysadmins@lists.eecs.berkeley.edu
- **Request**: `libpq-dev` package (provides `pg_config` needed to build psycopg2 from source)

#### 1.3.5 Install Apache Airflow

**IMPORTANT**: Apache Airflow is not included in the base Hammer dependencies. You must install it separately to get Airflow 3.1.0.

```bash
# Make sure you're in the Hammer directory
cd /bwrcq/C/<username>/hammer

# Activate the virtual environment first
source .venv/bin/activate

# Set Airflow home directory
export AIRFLOW_HOME=$(pwd)
echo "AIRFLOW home directory set to: $AIRFLOW_HOME"

# Define versions
AIRFLOW_VERSION=3.1.0
PYTHON_VERSION="$(python -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")')"
CONSTRAINT_URL="https://raw.githubusercontent.com/apache/airflow/constraints-${AIRFLOW_VERSION}/constraints-${PYTHON_VERSION}.txt"

# Clean up doc-only packages that conflict with Airflow
echo "Removing doc-related packages to avoid version conflicts..."
pip uninstall -y myst-parser mdit-py-plugins markdown-it-py || true

# Install Airflow with constraint file (ensures compatible dependencies)
echo "Installing Apache Airflow ${AIRFLOW_VERSION} (Python ${PYTHON_VERSION})"
pip install "apache-airflow==${AIRFLOW_VERSION}" --constraint "${CONSTRAINT_URL}"

echo "Airflow installed successfully."

# Verify installation
airflow version
# Should show: Apache Airflow 3.1.0
```

**Note**: The constraint file ensures all Airflow dependencies are compatible versions. This is the recommended installation method from Apache Airflow.

#### 1.4 Activate Virtual Environment and Initialize Airflow

```bash
# Activate the virtual environment
source .venv/bin/activate

# Set Airflow home directory
export AIRFLOW_HOME=$(pwd)

# Verify Airflow is installed (this also creates airflow.cfg automatically)
airflow version
# Should show: Apache Airflow 3.1.0

# Note: Running 'airflow version' creates airflow.cfg in AIRFLOW_HOME if it doesn't exist
```

**Tip**: You can create and use a `venv.sh` script to quickly activate the environment:
```bash
# Create venv.sh script with exact contents
cat > venv.sh << 'EOF'
source ./.venv/bin/activate

# Set AIRFLOW_HOME to current directory (Hammer root)
export AIRFLOW_HOME=$(pwd)

echo "Virtual environment activated."
echo "AIRFLOW_HOME set to: $AIRFLOW_HOME"
EOF

# Make it executable
chmod +x venv.sh

# Use it to activate environment
source venv.sh
```

This will create a `venv.sh` file with the exact contents shown above. The script activates the virtual environment and sets `AIRFLOW_HOME` to the current directory.

### Step 2: Set Up PostgreSQL Connection

#### 2.1 Get PostgreSQL Password

1. **Check your @berkeley.edu email** for a message from the server administrator
2. **Click the link** in the email (verification code will be sent)
3. **Retrieve your PostgreSQL password** from the link

#### 2.2 Update Password in airflow.cfg

Edit `airflow.cfg` and locate the `[database]` section:

```ini
[database]
sql_alchemy_conn = postgresql+psycopg2://<username>:YOUR_PASSWORD@barney.eecs.berkeley.edu:5433/airflow_<username>
```

Replace `<username>` with your actual username and `YOUR_PASSWORD` with your actual PostgreSQL password.

**Current Configuration:**
- Host: `barney.eecs.berkeley.edu`
- Port: `5433`
- Database: `airflow_<username>` (e.g., `airflow_lawrencejrhee`)
- User: `<username>` (e.g., `lawrencejrhee`)

#### 2.3 Test PostgreSQL Connection

```bash
cd /bwrcq/C/<username>/hammer
source .venv/bin/activate
export AIRFLOW_HOME=$(pwd)

# Test connection
airflow db check

# Or use test script if available
./test_postgres_connection.sh
```

If connection fails, see [Troubleshooting](#troubleshooting) section.

### Step 3: Configure Airflow

#### 3.1 Set Airflow Home

```bash
export AIRFLOW_HOME=/bwrcq/C/<username>/hammer
# Or use current directory:
export AIRFLOW_HOME=$(pwd)
```

#### 3.2 Critical Configuration Fixes

Airflow 3.1.0 requires specific configuration for LocalExecutor to work properly. These fixes are **essential**:

**Edit `airflow.cfg`:**

1. **API Base URL (CRITICAL FIX)** - Required for task execution
   ```ini
   [api]
   base_url = http://localhost:8090
   ```
   **Why**: The execution-time supervisor needs to know where the API server is located. Without this, tasks fail with "Network is unreachable" errors.

2. **Core Configuration** - Optimize for stability
   ```ini
   [core]
   parallelism = 1  # Start with 1, can increase later
   max_active_tasks_per_dag = 1  # Start with 1, can increase later
   executor = LocalExecutor
   dags_folder = /bwrcq/C/<username>/hammer/dags
   ```

3. **Database Connection Pooling** - Improve reliability
   ```ini
   [database]
   sql_alchemy_pool_size = 10  # Increased from 5
   sql_alchemy_max_overflow = 20  # Increased from 10
   sql_alchemy_engine_args = {"pool_pre_ping": true}
   ```

4. **Logging Configuration** - Disable worker log server (optional, prevents network issues)
   ```ini
   [logging]
   worker_log_server_port =   # Leave empty to disable
   ```

5. **Scheduler Configuration** - Increase timeouts
   ```ini
   [scheduler]
   task_queued_timeout = 1200.0  # Increased from 600.0
   job_heartbeat_sec = 10  # Increased from 5
   scheduler_heartbeat_sec = 10  # Increased from 5
   task_instance_heartbeat_sec = 30  # Changed from 0
   task_instance_heartbeat_timeout = 600  # Increased from 300
   ```

6. **Deprecation Fixes** - For Airflow 3.1.0 compatibility
   
   **Note**: If you're upgrading from Airflow 2.x, you may need to update these settings. For fresh Airflow 3.1.0 installations, these are already correct:
   ```ini
   [api]
   log_config = -  # Already correct in Airflow 3.1.0 (was access_logfile in 2.x)
   grid_view_sorting_order = topological  # Already in [api] section in 3.1.0 (was in [webserver] in 2.x)
   ```
   
   If you're doing a fresh install, Airflow 3.1.0 will create these correctly automatically. Only update if you're migrating from an older version.

**Complete Critical Sections Reference**:

For your reference, here are the complete critical sections from `airflow.cfg` that must be configured correctly. Copy these exact sections into your `airflow.cfg`:

```ini
[core]
# Executor type
executor = LocalExecutor

# DAGs folder (update with your actual path)
dags_folder = /bwrcq/C/<username>/hammer/dags

# Parallelism settings (start with 1, can increase later)
parallelism = 1
max_active_tasks_per_dag = 1

[database]
# PostgreSQL connection string (replace YOUR_PASSWORD with actual password)
sql_alchemy_conn = postgresql+psycopg2://<username>:YOUR_PASSWORD@barney.eecs.berkeley.edu:5433/airflow_<username>

# Connection pooling settings
sql_alchemy_pool_size = 10
sql_alchemy_max_overflow = 20
sql_alchemy_engine_args = {"pool_pre_ping": true}

[api]
# CRITICAL: Base URL for API server (required for task execution)
base_url = http://localhost:8090

# Deprecation fixes (already correct in Airflow 3.1.0, only needed if upgrading from 2.x)
log_config = -  # Already correct in Airflow 3.1.0 (was access_logfile in 2.x)
grid_view_sorting_order = topological  # Already in [api] section in 3.1.0 (was in [webserver] in 2.x)

[scheduler]
# Timeout settings
task_queued_timeout = 1200.0

# Heartbeat settings
job_heartbeat_sec = 10
scheduler_heartbeat_sec = 10
task_instance_heartbeat_sec = 30
task_instance_heartbeat_timeout = 600

[logging]
# Disable worker log server (optional, prevents network issues)
worker_log_server_port =   # Leave empty to disable
```

**Important**: Replace `<username>` with your actual username in the paths and connection strings above.

#### 3.3 Verify Configuration

```bash
# Check database connection string
airflow config get-value database sql_alchemy_conn

# Check API base URL
airflow config get-value api base_url

# Check executor
airflow config get-value core executor
```

### Step 4: Initialize Database

#### 4.1 Check Database Status

```bash
cd /bwrcq/C/<username>/hammer
source .venv/bin/activate
export AIRFLOW_HOME=$(pwd)

# Check if database schema exists
airflow db check-migrations
```

#### 4.2 Initialize Database Schema

```bash
# Initialize database schema
airflow db migrate

# Verify initialization
airflow db check
airflow db check-migrations
```

The initialization will:
- Create all Airflow metadata tables
- Set up indexes and constraints
- Apply all migrations

#### 4.3 Verify Database Tables

```bash
# Connect to PostgreSQL and check tables
psql -h barney.eecs.berkeley.edu -p 5433 -U <username> -d airflow_<username> -c "\dt"

# Should show Airflow tables like:
# - dag
# - dag_run
# - task_instance
# - job
# - log
# - xcom
# etc.
```

### Step 5: Create Admin User

Create an admin user for the Airflow web UI:

```bash
cd /bwrcq/C/<username>/hammer
source .venv/bin/activate
export AIRFLOW_HOME=$(pwd)

airflow users create \
  --username admin \
  --firstname Admin \
  --lastname User \
  --role Admin \
  --email admin@example.com \
  --password admin
```

**Note**: Change the password from `admin` to something secure in production!

### Step 6: Start Airflow

#### 6.1 Stop Any Existing Airflow Processes

```bash
# Check for running processes
ps aux | grep airflow | grep -v grep

# Stop all Airflow processes
pkill -9 -f airflow

# Verify stopped
ps aux | grep airflow | grep -v grep || echo "✓ No Airflow processes running"
```

#### 6.2 Start Airflow Standalone (Recommended)

```bash
cd /bwrcq/C/<username>/hammer
source .venv/bin/activate
export AIRFLOW_HOME=$(pwd)

# Start Airflow (scheduler + webserver together)
airflow standalone
```

Wait for the message: `Airflow is ready`

#### 6.3 Start Airflow Separately (Alternative)

**Terminal 1 - Scheduler:**
```bash
cd /bwrcq/C/<username>/hammer
source .venv/bin/activate
export AIRFLOW_HOME=$(pwd)
airflow scheduler
```

**Terminal 2 - Webserver:**
```bash
cd /bwrcq/C/<username>/hammer
source .venv/bin/activate
export AIRFLOW_HOME=$(pwd)
airflow webserver --port 8090
```

#### 6.4 Access Airflow UI

**Option A: Access from Remote Machine (NoMachine/X2Go)**

If you're using a remote desktop connection to the BWRC machine:
- **URL**: http://localhost:8090
- **Username**: `admin`
- **Password**: `admin` (or whatever you set in Step 5)

**Option B: Access from Local Machine via SSH Port Forwarding**

If you want to access the Airflow UI from your local laptop:

1. **On your local machine**, set up SSH port forwarding:
   ```bash
   ssh -A -L <local_port>:localhost:8090 -J <username>@bwrcrdsl-1.eecs.berkeley.edu <username>@<compute_node>.eecs.berkeley.edu
   
   Replace `<compute_node>` with the actual compute node you're using (e.g., `bwrcix-3`, `bwrcix-4`, etc.)
   ```
   
   Replace:
   - `<local_port>`: Any unused port on your local machine (e.g., 8090)
   - `<username>`: Your LDAP username
   - `8090`: The port Airflow is running on (from `airflow.cfg`)

2. **In your local browser**, navigate to:
   - **URL**: http://localhost:<local_port>
   - **Username**: `admin`
   - **Password**: `admin` (or whatever you set in Step 5)

**Note**: The SSH port forwarding command creates a tunnel from your local machine to the remote Airflow server, allowing you to access it as if it were running locally.

## Configuration Details

### Connection String Format

```ini
sql_alchemy_conn = postgresql+psycopg2://username:password@host:port/database
```

**Components:**
- `postgresql+psycopg2`: Database dialect and driver
- `username`: PostgreSQL username
- `password`: PostgreSQL password
- `host`: PostgreSQL server hostname
- `port`: PostgreSQL server port
- `database`: Database name

### Environment Variables

Set `AIRFLOW_HOME` to your Airflow directory:

```bash
export AIRFLOW_HOME=/bwrcq/C/<username>/hammer
```

Or let scripts set it automatically (defaults to current directory).

### DAG Folder Configuration

Ensure `dags_folder` in `airflow.cfg` points to your DAGs:

```ini
[core]
dags_folder = /bwrcq/C/<username>/hammer/dags
```

## Verification

### Quick Verification

Run the verification script to check database health:

```bash
./verify_postgres.sh
```

This script checks:
1. **Connection**: Can Airflow connect to PostgreSQL?
2. **Schema Status**: Are migrations applied?
3. **Tables**: Are Airflow metadata tables present?

### Manual Verification

```bash
# Test connection
airflow db check

# Check migrations
airflow db check-migrations

# Access database shell
airflow db shell
```

### Verify Metadata Storage

To verify that metadata (task instances, DAG runs, logs) is being stored correctly:

```bash
cd /bwrcq/C/<username>/hammer
source .venv/bin/activate
export AIRFLOW_HOME=$(pwd)

python3 << 'EOF'
from sqlalchemy import create_engine, text
from airflow.configuration import conf

engine = create_engine(conf.get('database', 'sql_alchemy_conn'))
with engine.connect() as conn:
    # Check DAG runs
    result = conn.execute(text("SELECT COUNT(*) FROM dag_run"))
    print(f"DAG runs: {result.fetchone()[0]}")
    
    # Check task instances
    result = conn.execute(text("SELECT COUNT(*) FROM task_instance"))
    print(f"Task instances: {result.fetchone()[0]}")
    
    # Check successful tasks
    result = conn.execute(text("SELECT COUNT(*) FROM task_instance WHERE state = 'success'"))
    print(f"Successful tasks: {result.fetchone()[0]}")
    
    # Check logs
    result = conn.execute(text("SELECT COUNT(*) FROM log"))
    print(f"Log entries: {result.fetchone()[0]}")
EOF
```


## Usage

### Running DAGs

Once Airflow is running with PostgreSQL:
- Multiple tasks can run in parallel
- Multiple users can access the system simultaneously
- DAGs will execute with improved performance and reliability

### Creating DAGs

Create DAG files in the `dags/` directory:

```python
from datetime import datetime, timedelta
from airflow import DAG
from airflow.providers.standard.operators.bash import BashOperator
from airflow.providers.standard.operators.python import PythonOperator

with DAG(
    'my_dag',
    schedule=timedelta(days=1),  # Note: use 'schedule' not 'schedule_interval' in Airflow 3.x
    start_date=datetime(2024, 1, 1),
    catchup=False,
) as dag:
    
    task1 = BashOperator(
        task_id='task1',
        bash_command='echo "Hello from Airflow!"',
    )
    
    task1
```

**Important**: Use provider imports for Airflow 3.x:
- `from airflow.providers.standard.operators.bash import BashOperator`
- `from airflow.providers.standard.operators.python import PythonOperator`

### Triggering DAGs

**Via Web UI:**
1. Navigate to DAGs list
2. Click the play button (▶️) next to your DAG
3. Select "Trigger DAG"

**Via CLI:**
```bash
airflow dags trigger my_dag
```

## Troubleshooting

### Connection Refused

**Error**: `could not connect to server: Connection refused`

**Solutions**:
1. Verify PostgreSQL server is running
2. Check network connectivity: `ping barney.eecs.berkeley.edu`
3. Verify port is correct: `telnet barney.eecs.berkeley.edu 5433`
4. If the server is down, contact:
   - **Email**: anne_young@berkeley.edu
   - **System Admins**: bwrc-sysadmins@lists.eecs.berkeley.edu
5. Check firewall rules
6. Verify connection string in `airflow.cfg`
7. Ensure you're on a compute node (any available one), not the login server

### Authentication Failed

**Error**: `password authentication failed`

**Solutions**:
1. Verify username and password in connection string
2. Re-check password from email link
3. Make sure password has no extra spaces when copying
4. If you need PostgreSQL user permissions or database access, contact:
   - **Email**: anne_young@berkeley.edu
   - **System Admins**: bwrc-sysadmins@lists.eecs.berkeley.edu

### Database Does Not Exist

**Error**: `database "airflow_<username>" does not exist`

**Note**: This error is uncommon. If you encounter it, the database needs to be created by a PostgreSQL administrator with administrative privileges.

**Solution**:
Contact the system administrators to request database creation:
- **Email**: anne_young@berkeley.edu
- **System Admins**: bwrc-sysadmins@lists.eecs.berkeley.edu

Provide them with:
- Your username
- The database name you need (e.g., `airflow_<username>`)
- Your PostgreSQL username (if different from your system username)

### Network Unreachable Error

**Error**: `httpcore.ConnectError: [Errno 101] Network is unreachable`

**Solution**: This is a critical configuration issue. Ensure `base_url` is set in `[api]` section:
```ini
[api]
base_url = http://localhost:8090
```


### State Mismatch Errors

**Error**: `Executor reported task finished with state failed, but task instance's state attribute is queued`

**Solutions**:
1. Ensure only one scheduler is running: `ps aux | grep "airflow.*scheduler"`
2. Stop all Airflow processes and restart cleanly
3. Verify `base_url` is configured correctly
4. Check for multiple Airflow instances hitting the same database
5. Review Airflow and PostgreSQL logs for detailed error messages

### Tasks Not Starting

**Symptoms**: Tasks stuck in `queued` state, no logs, `start_date` is NULL

**Solutions**:
1. Verify `base_url` is set in `[api]` section (CRITICAL)
2. Check that only one scheduler is running
3. Verify database connection is working: `airflow db check`
4. Check scheduler logs for errors
5. Ensure DAG is not paused in the UI

### Migrations Pending

**Error**: `There are still unapplied migrations`

**Solutions**:
1. Run migrations: `airflow db migrate`
2. If migrations fail, check for conflicts
3. If you need database permissions or encounter permission errors, contact:
   - **Email**: anne_young@berkeley.edu
   - **System Admins**: bwrc-sysadmins@lists.eecs.berkeley.edu
4. Check Airflow version compatibility

### Schema Not Initialized

**Error**: `Migration Head(s) in DB: set()`

**Solution**:
Run database initialization:
```bash
airflow db migrate
```

### Port Already in Use

**Error**: `[Errno 98] error while attempting to bind on address ('0.0.0.0', 8090): address already in use`

**Solutions**:
1. Find process using port: `lsof -i :8090`
2. Kill the process: `kill -9 <PID>`
3. Or use different port: `airflow webserver --port 8091`

## Migration from SQLite

If you have an existing SQLite database (`airflow.db`), you can migrate to PostgreSQL:

### Option 1: Fresh Start (Recommended)

1. Backup existing SQLite database (if needed):
   ```bash
   cp airflow.db airflow.db.backup
   ```

2. Configure PostgreSQL connection in `airflow.cfg`

3. Initialize PostgreSQL database:
   ```bash
   airflow db migrate
   ```

4. Recreate DAGs and configurations as needed

### Option 2: Data Migration

For complex migrations, you may need to:
1. Export data from SQLite
2. Transform data format
3. Import into PostgreSQL

**Note**: Airflow doesn't provide built-in SQLite-to-PostgreSQL migration tools. For production systems, consider using database migration tools or manual export/import.

## Best Practices

1. **Regular Backups**: Backup your PostgreSQL database regularly
2. **Monitor Connections**: Use PostgreSQL monitoring tools to track connection usage
3. **Connection Pooling**: PostgreSQL handles connection pooling automatically via `sql_alchemy_pool_size`
4. **Migration Management**: Always test migrations in a development environment first
5. **Documentation**: Keep connection strings and credentials secure
6. **Single Scheduler**: Ensure only one scheduler instance is running
7. **Configuration**: Always set `base_url` in `[api]` section for Airflow 3.1.0+

## Additional Resources

- [Airflow Database Setup](https://airflow.apache.org/docs/apache-airflow/stable/howto/set-up-database.html)
- [PostgreSQL Documentation](https://www.postgresql.org/docs/)
- [psycopg2 Documentation](https://www.psycopg.org/docs/)

## Support

For issues specific to SledgeHammer PostgreSQL integration:
1. Check this documentation
2. Run `./verify_postgres.sh` for diagnostics
3. If the PostgreSQL server is down, contact:
   - **Email**: anne_young@berkeley.edu
   - **System Admins**: bwrc-sysadmins@lists.eecs.berkeley.edu
4. Review Airflow logs in `logs/` directory
5. Check PostgreSQL server logs
6. Review the Troubleshooting section above for common issues and solutions

---

**Author**: Lawrence Rhee  
**Date**: January 6, 2026  
**Last Updated**: January 6, 2026  
**Airflow Version**: 3.1.0  
**PostgreSQL Version**: 16.4 (on server)  
**Python Version**: 3.10-3.13
