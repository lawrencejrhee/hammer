# PostgreSQL Setup for Apache Airflow

This README provides a complete guide for setting up **Apache Airflow 3.1.0** with a **PostgreSQL** backend in a standard environment (local machine, VM, or cloud instance). It replaces the default SQLite backend to support concurrent connections, parallel task execution, and multi user operation.

## Table of Contents

* [Overview](#overview)
* [Prerequisites](#prerequisites)
* [Quick Start](#quick-start)
* [Setup](#setup)

  * [1. Create Project Directory](#1-create-project-directory)
  * [2. Install Poetry](#2-install-poetry)
  * [3. Create pyprojecttoml](#3-create-pyprojecttoml)
  * [4. Install Dependencies](#4-install-dependencies)
  * [5. Initialize Airflow](#5-initialize-airflow)
* [PostgreSQL Setup](#postgresql-setup)

  * [1. Create Database and User](#1-create-database-and-user)
  * [2. Configure airflowcfg](#2-configure-airflowcfg)
  * [3. Verify Database Connection](#3-verify-database-connection)
* [Initialize Database Schema](#initialize-database-schema)
* [Create Admin User](#create-admin-user)
* [Run Airflow](#run-airflow)
* [Access the Web UI](#access-the-web-ui)
* [Verification](#verification)
* [Troubleshooting](#troubleshooting)
* [Migration from SQLite](#migration-from-sqlite)
* [Best Practices](#best-practices)
* [Generic Setup Guide for Any User](#generic-setup-guide-for-any-user)

---

## Overview

PostgreSQL provides the foundation for:

* **Concurrent connections**: multiple tasks and users can access the database simultaneously
* **Parallel execution**: avoids SQLite locking issues when running tasks in parallel
* **Scalability**: suitable for larger deployments and shared servers
* **Multi user collaboration**: multiple users can access the same Airflow instance

---

## Prerequisites

You need:

* **Python** 3.10 through 3.13 (recommended 3.11)
* **PostgreSQL** 13 or newer (local or remote)
* **Poetry** (dependency management)
* A working build toolchain for `psycopg2` if wheels are unavailable

  * Linux often needs `libpq-dev` and `gcc`
  * macOS often needs Xcode command line tools

Check Python:

```bash
python3 --version
```

---

## Quick Start

If PostgreSQL is already running and you already have a database and user:

```bash
mkdir airflow-postgres && cd airflow-postgres
curl -sSL https://install.python-poetry.org | python3 -
export PATH="$HOME/.local/bin:$PATH"
poetry config virtualenvs.in-project true

# create pyproject.toml (see below), then
poetry install
source .venv/bin/activate
export AIRFLOW_HOME=$(pwd)

airflow version
airflow db migrate
airflow users create --username admin --firstname Admin --lastname User --role Admin --email admin@example.com --password admin
airflow standalone
```

---

## Setup

### 1. Create Project Directory

```bash
mkdir airflow-postgres
cd airflow-postgres
```

Configure Poetry venv location:

```bash
poetry config virtualenvs.in-project true
```

---

### 2. Install Poetry

```bash
curl -sSL https://install.python-poetry.org | python3 -
export PATH="$HOME/.local/bin:$PATH"
poetry --version
```

---

### 3. Create pyprojecttoml

Create a `pyproject.toml` in the project root:

```toml
[tool.poetry]
name = "airflow-postgres"
version = "0.1.0"
description = "Apache Airflow with PostgreSQL backend"
authors = ["Your Name"]

[tool.poetry.dependencies]
python = ">=3.10,<3.14"
apache-airflow = "==3.1.0"
psycopg2 = "^2.9.11"
asyncpg = "^0.30.0"
pydantic = "^2.12.2"

[build-system]
requires = ["poetry-core>=1.0.0"]
build-backend = "poetry.core.masonry.api"
```

---

### 4. Install Dependencies

```bash
poetry lock
poetry install
source .venv/bin/activate
```

Set Airflow home:

```bash
export AIRFLOW_HOME=$(pwd)
```

---

### 5. Initialize Airflow

Verify Airflow is installed:

```bash
airflow version
```

This creates `airflow.cfg` in `$AIRFLOW_HOME` if it does not exist.

---

## PostgreSQL Setup

### 1. Create Database and User

Run these commands on your PostgreSQL server using `psql` as a PostgreSQL admin user:

```sql
CREATE DATABASE airflow;
CREATE USER airflow WITH PASSWORD 'strong_password';
GRANT ALL PRIVILEGES ON DATABASE airflow TO airflow;
```

If you are using a managed PostgreSQL service, create the database and user through its console.

---

### 2. Configure airflowcfg

Edit `airflow.cfg` and set these sections.

#### Database

```ini
[database]
sql_alchemy_conn = postgresql+psycopg2://airflow:strong_password@localhost:5432/airflow
sql_alchemy_pool_size = 10
sql_alchemy_max_overflow = 20
sql_alchemy_engine_args = {"pool_pre_ping": true}
```

Replace:

* `airflow` username
* `strong_password`
* `localhost`
* `5432`
* database name

---

#### Core

```ini
[core]
executor = LocalExecutor
parallelism = 1
max_active_tasks_per_dag = 1
dags_folder = %(airflow_home)s/dags
```

---

#### API (Critical)

```ini
[api]
base_url = http://localhost:8080
```

Airflow 3.1.0 requires this for task execution.

---

#### Scheduler

```ini
[scheduler]
task_queued_timeout = 1200
job_heartbeat_sec = 10
scheduler_heartbeat_sec = 10
task_instance_heartbeat_sec = 30
task_instance_heartbeat_timeout = 600
```

---

#### Logging (Optional)

```ini
[logging]
worker_log_server_port =
```

Leaving this empty disables the worker log server.

---

### 3. Verify Database Connection

```bash
airflow db check
```

---

## Initialize Database Schema

Apply migrations:

```bash
airflow db migrate
```

Verify:

```bash
airflow db check
airflow db check-migrations
```

---

## Create Admin User

```bash
airflow users create \
  --username admin \
  --firstname Admin \
  --lastname User \
  --role Admin \
  --email admin@example.com \
  --password admin
```

Use a stronger password in real deployments.

---

## Run Airflow

### Option A: Standalone (Recommended)

```bash
airflow standalone
```

### Option B: Separate Processes

Terminal 1:

```bash
airflow scheduler
```

Terminal 2:

```bash
airflow webserver --port 8080
```

---

## Access the Web UI

Open:

```text
http://localhost:8080
```

Login with the admin credentials you created.

If Airflow is running on a remote machine, use SSH port forwarding:

```bash
ssh -L 8080:localhost:8080 your_user@your_remote_host
```

Then open:

```text
http://localhost:8080
```

---

## Verification

Useful checks:

```bash
airflow config get-value database sql_alchemy_conn
airflow config get-value api base_url
airflow config get-value core executor

airflow dags list
airflow users list
airflow db check
```

---

## Troubleshooting

### Connection refused

* Confirm PostgreSQL is running
* Confirm host and port are correct
* Confirm firewall and security groups allow access
* Test with:

```bash
psql -h localhost -p 5432 -U airflow -d airflow
```

### Password authentication failed

* Verify username and password in `sql_alchemy_conn`
* Check for special characters that need URL encoding

### Tasks stuck queued or network unreachable

Ensure this is set:

```ini
[api]
base_url = http://localhost:8080
```

Also ensure you are not running multiple schedulers against the same metadata DB.

### Port already in use

Pick a different port:

```bash
airflow webserver --port 8081
```

Update:

```ini
[api]
base_url = http://localhost:8081
```

---

## Migration from SQLite

A fresh start is recommended.

If you previously used SQLite and want to move to PostgreSQL:

1. Stop Airflow
2. Update `sql_alchemy_conn` to PostgreSQL
3. Run:

```bash
airflow db migrate
```

Airflow does not provide a full SQLite to PostgreSQL metadata migration tool.

---

## Best Practices

* Use PostgreSQL or MySQL in any real deployment
* Avoid SQLite for parallelism or multi user setups
* Keep credentials out of Git
* Back up PostgreSQL regularly
* Run only one scheduler per metadata database
* Start with low parallelism, then increase gradually

---

## Generic Setup Guide for Any User

This section provides a complete, step-by-step setup guide that works for **any user on any system** (local machine, remote server, cloud instance, etc.), regardless of your username, server name, or directory structure.

### Step 1: Choose Your Working Directory

Navigate to wherever you want to set up Airflow. This can be any directory on any system:

```bash
# Option 1: Create a new directory in your home folder
cd ~
mkdir my-airflow-project
cd my-airflow-project

# Option 2: Use an existing directory
cd /path/to/your/desired/location
mkdir airflow-setup
cd airflow-setup

# Option 3: Use your current directory
# Just stay where you are
```

**Note**: Replace paths above with your actual desired location. The setup will work from any directory.

### Step 2: Install Poetry (If Not Already Installed)

Check if Poetry is installed:

```bash
poetry --version
```

If not installed, install it:

```bash
curl -sSL https://install.python-poetry.org | python3 -
export PATH="$HOME/.local/bin:$PATH"
```

**For macOS users**: If `$HOME/.local/bin` doesn't work, you may need to add Poetry to your PATH differently. Check your shell configuration file (`~/.zshrc` or `~/.bash_profile`):

```bash
echo 'export PATH="$HOME/.local/bin:$PATH"' >> ~/.zshrc  # for zsh
# or
echo 'export PATH="$HOME/.local/bin:$PATH"' >> ~/.bash_profile  # for bash
source ~/.zshrc  # or source ~/.bash_profile
```

### Step 3: Configure Poetry and Create Project Structure

In your chosen directory:

```bash
# Configure Poetry to create virtual environment in the project
poetry config virtualenvs.in-project true

# Create the pyproject.toml file
cat > pyproject.toml << 'EOF'
[tool.poetry]
name = "airflow-postgres"
version = "0.1.0"
description = "Apache Airflow with PostgreSQL backend"
authors = ["Your Name <your.email@example.com>"]

[tool.poetry.dependencies]
python = ">=3.10,<3.14"
apache-airflow = "==3.1.0"
psycopg2 = "^2.9.11"
asyncpg = "^0.30.0"
pydantic = "^2.12.2"

[build-system]
requires = ["poetry-core>=1.0.0"]
build-backend = "poetry.core.masonry.api"
EOF
```

**Customization**: Edit the `authors` field with your actual name and email.

### Step 4: Install Dependencies

```bash
# Install dependencies (this may take several minutes)
poetry install

# Activate the virtual environment
source .venv/bin/activate

# Set AIRFLOW_HOME to current directory
export AIRFLOW_HOME=$(pwd)
```

**Note**: The `AIRFLOW_HOME` environment variable tells Airflow where to store its configuration and data. You can set this to any directory you prefer.

### Step 5: Set Up PostgreSQL

#### Option A: Local PostgreSQL Installation

If PostgreSQL is installed locally on your machine:

```bash
# Check if PostgreSQL is running (Linux/macOS)
sudo systemctl status postgresql  # Linux
# or
brew services list | grep postgresql  # macOS with Homebrew

# Connect as postgres superuser
sudo -u postgres psql  # Linux
# or
psql postgres  # macOS (if installed via Homebrew)
```

Then in the PostgreSQL prompt:

```sql
CREATE DATABASE airflow;
CREATE USER airflow_user WITH PASSWORD 'your_secure_password_here';
GRANT ALL PRIVILEGES ON DATABASE airflow TO airflow_user;
\q
```

**Important**: Replace `your_secure_password_here` with a strong password of your choice.

#### Option B: Remote PostgreSQL Server

If you're using a remote PostgreSQL server (cloud provider, separate server, etc.):

1. Get connection details from your provider:
   - Host: `your-db-host.example.com` or IP address
   - Port: Usually `5432` (default)
   - Database name: Create one or use existing
   - Username: Your database user
   - Password: Your database password

2. Connect to create the database (if needed):

```bash
psql -h your-db-host.example.com -p 5432 -U your_admin_user -d postgres
```

Then:

```sql
CREATE DATABASE airflow;
CREATE USER airflow_user WITH PASSWORD 'your_secure_password_here';
GRANT ALL PRIVILEGES ON DATABASE airflow TO airflow_user;
\q
```

#### Option C: Managed PostgreSQL Service

If using a managed service (AWS RDS, Google Cloud SQL, Azure Database, etc.):

1. Create the database and user through the service's web console
2. Note down the connection details (host, port, database name, username, password)

### Step 6: Configure Airflow

With your virtual environment activated and `AIRFLOW_HOME` set:

```bash
# Initialize Airflow (creates airflow.cfg)
airflow version
```

Now edit `airflow.cfg`. Find the `[database]` section and update `sql_alchemy_conn`:

```ini
[database]
sql_alchemy_conn = postgresql+psycopg2://airflow_user:your_secure_password_here@localhost:5432/airflow
sql_alchemy_pool_size = 10
sql_alchemy_max_overflow = 20
sql_alchemy_engine_args = {"pool_pre_ping": true}
```

**Connection String Format**:
```
postgresql+psycopg2://USERNAME:PASSWORD@HOST:PORT/DATABASE_NAME
```

**Examples**:
- Local: `postgresql+psycopg2://airflow_user:mypass@localhost:5432/airflow`
- Remote: `postgresql+psycopg2://airflow_user:mypass@db.example.com:5432/airflow`
- With special characters in password: URL-encode them (e.g., `@` becomes `%40`)

Also update these sections:

```ini
[core]
executor = LocalExecutor
parallelism = 1
max_active_tasks_per_dag = 1
dags_folder = %(airflow_home)s/dags

[api]
base_url = http://localhost:8080

[scheduler]
task_queued_timeout = 1200
job_heartbeat_sec = 10
scheduler_heartbeat_sec = 10
task_instance_heartbeat_sec = 30
task_instance_heartbeat_timeout = 600
```

**For remote servers**: If Airflow is running on a remote machine and you want to access the web UI, you may need to set `base_url` to the server's hostname or IP:

```ini
[api]
base_url = http://your-server-ip:8080
# or
base_url = http://your-server-hostname:8080
```

### Step 7: Initialize Database and Create Admin User

```bash
# Verify database connection
airflow db check

# Initialize database schema
airflow db migrate

# Create admin user (replace with your preferred credentials)
airflow users create \
  --username admin \
  --firstname YourFirstName \
  --lastname YourLastName \
  --role Admin \
  --email your.email@example.com \
  --password your_admin_password
```

**Customization**: Replace all the user details with your actual information.

### Step 8: Start Airflow

#### For Local Development (Same Machine):

```bash
# Option 1: Standalone (easiest)
airflow standalone

# Option 2: Separate processes (two terminals)
# Terminal 1:
airflow scheduler

# Terminal 2:
airflow webserver --port 8080
```

Access at: `http://localhost:8080`

#### For Remote Server:

```bash
# Start Airflow
airflow standalone

# Or use separate processes
airflow scheduler &
airflow webserver --port 8080 --hostname 0.0.0.0 &
```

**Access Options**:

1. **SSH Port Forwarding** (recommended for security):
   ```bash
   # On your local machine
   ssh -L 8080:localhost:8080 your_username@your_server_hostname
   ```
   Then open `http://localhost:8080` in your browser.

2. **Direct Access** (if firewall allows):
   Open `http://your-server-ip:8080` or `http://your-server-hostname:8080` in your browser.

### Step 9: Verify Everything Works

```bash
# Check configuration
airflow config get-value database sql_alchemy_conn
airflow config get-value api base_url

# List DAGs
airflow dags list

# List users
airflow users list

# Check database connection
airflow db check
```

### Environment Variables (Optional but Recommended)

To make your setup persistent, add these to your shell configuration file:

```bash
# Add to ~/.bashrc, ~/.zshrc, or ~/.bash_profile
export AIRFLOW_HOME=/path/to/your/airflow/project
export PATH="$HOME/.local/bin:$PATH"
```

Then reload:
```bash
source ~/.bashrc  # or ~/.zshrc, or ~/.bash_profile
```

### Troubleshooting for Generic Setup

#### "Command not found: poetry"
- Ensure Poetry is installed and in your PATH
- Check: `echo $PATH | grep poetry` or `which poetry`
- Re-run Poetry installation if needed

#### "Command not found: airflow"
- Ensure virtual environment is activated: `source .venv/bin/activate`
- Verify installation: `poetry show apache-airflow`

#### "Can't connect to PostgreSQL"
- Verify PostgreSQL is running
- Check host, port, username, password in connection string
- Test connection: `psql -h HOST -p PORT -U USERNAME -d DATABASE_NAME`

#### "Port 8080 already in use"
- Use a different port: `airflow webserver --port 8081`
- Update `base_url` in `airflow.cfg` accordingly

#### "Permission denied" errors
- Check file permissions in your project directory
- Ensure you have write access to `AIRFLOW_HOME`

### Next Steps

1. Create your first DAG in `$AIRFLOW_HOME/dags/`
2. Monitor tasks in the Airflow web UI
3. Adjust parallelism settings in `airflow.cfg` as needed
4. Set up proper backups for your PostgreSQL database

---
