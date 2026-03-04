# SledgeHammer Setup with uv (PostgreSQL + Airflow)

This document provides a **complete from-scratch guide** for setting up Hammer with Apache Airflow and PostgreSQL using **[uv](https://github.com/astral-sh/uv)** instead of Poetry. `uv` is an extremely fast Python package manager written in Rust that replaces `pip`, `poetry`, `virtualenv`, and more.

**Why uv instead of Poetry?**
- **10-100x faster** than pip/poetry for dependency resolution and installation
- **Single tool**: replaces pip, poetry, virtualenv, and pyenv
- **Simple installation**: one `curl` command, no Python bootstrap needed
- **Standard format**: uses PEP 621 `pyproject.toml` (no proprietary `[tool.poetry]` format)
- **Source builds just work**: `--no-binary` flag compiles C extensions from source cleanly

## Table of Contents

- [Prerequisites](#prerequisites)
- [Complete Setup Process](#complete-setup-process)
  - [Step 0: Install Python (If Not Already Installed)](#step-0-install-python-if-not-already-installed)
  - [Step 0.3: Set Up GitHub SSH Access](#step-03-set-up-github-ssh-access)
  - [Step 0.4: Create Working Directory](#step-04-create-working-directory)
  - [Step 0.5: Clone Hammer Repository](#step-05-clone-hammer-repository)
  - [Step 1: Install uv](#step-1-install-uv)
  - [Step 1.5: Set Up pg\_config for psycopg2](#step-15-set-up-pg_config-for-psycopg2)
  - [Step 2: Create Virtual Environment and Install Dependencies](#step-2-create-virtual-environment-and-install-dependencies)
  - [Step 3: Install Apache Airflow](#step-3-install-apache-airflow)
  - [Step 4: Set Up PostgreSQL Connection](#step-4-set-up-postgresql-connection)
  - [Step 5: Configure Airflow](#step-5-configure-airflow)
  - [Step 6: Initialize Database](#step-6-initialize-database)
  - [Step 7: Start Airflow](#step-7-start-airflow)
- [Daily Usage](#daily-usage)
- [uv Cheat Sheet (vs Poetry)](#uv-cheat-sheet-vs-poetry)
- [Verification](#verification)
- [Troubleshooting](#troubleshooting)

---

## Prerequisites

1. **SSH Access to BWRC Machines**:
   - **Login Server**: `bwrcrdsl-1.eecs.berkeley.edu` (for initial connection)
   - **Compute Node**: You should be on a BWRC compute node (e.g., `bwrcix-3`, `bwrcix-4`, etc.)
   ```bash
   # First, SSH to login server
   ssh <username>@bwrcrdsl-1.eecs.berkeley.edu
   
   # Then SSH to any available compute node
   ssh <username>@bwrcix-3.eecs.berkeley.edu
   ```
   
   **Note**: Airflow should run on compute nodes, not the login server.

2. **GitHub Account** with SSH access configured (see Step 0.3)

3. **PostgreSQL Server**: Access to `barney.eecs.berkeley.edu:5433`
   
   **If the PostgreSQL server is down**, contact:
   - **Email**: anne_young@berkeley.edu
   - **System Admins**: bwrc-sysadmins@lists.eecs.berkeley.edu

4. **Database**: A PostgreSQL database must exist (e.g., `airflow_<username>`)

5. **Python 3.10-3.13**: Required for Airflow 3.1.0

---

## Complete Setup Process

### Step 0: Install Python (If Not Already Installed)

If Python 3.10-3.13 is not already available on your system, build from source:

```bash
cd ~
wget https://www.python.org/ftp/python/3.11.9/Python-3.11.9.tgz
tar -xzf Python-3.11.9.tgz
cd Python-3.11.9

# Configure and build (this may take 10-30 minutes)
./configure --prefix=$HOME/python311 --enable-optimizations --with-ensurepip=yes
make -j$(nproc)
make install

# Clean up (optional, saves ~380MB)
cd ~
rm -rf Python-3.11.9 Python-3.11.9.tgz

# Add to PATH
echo 'export PATH="$HOME/python311/bin:$PATH"' >> ~/.bashrc
source ~/.bashrc

# Verify
python3 --version
# Should show: Python 3.11.9
```

### Step 0.3: Set Up GitHub SSH Access

1. **Generate SSH Key** (if you don't already have one):
   ```bash
   ssh-keygen -t ed25519 -C "your_email@berkeley.edu"
   ```

2. **Start SSH Agent and Add Key**:
   ```bash
   eval "$(ssh-agent -s)"
   ssh-add ~/.ssh/id_ed25519
   ```

3. **Copy Your Public Key**:
   ```bash
   cat ~/.ssh/id_ed25519.pub
   ```

4. **Add SSH Key to GitHub**: Go to GitHub → Settings → SSH and GPG keys → New SSH key → Paste key

5. **Verify**:
   ```bash
   ssh -T git@github.com
   # Should show: Hi <username>! You've successfully authenticated...
   ```

### Step 0.4: Create Working Directory

```bash
mkdir -p /bwrcq/C/<username>
cd /bwrcq/C/<username>
```

**Note**: Replace `<username>` with your actual LDAP username. Your home directory may be at `/bwrcq/home/<username>` or `/bwrcq/C/<username>`.

### Step 0.5: Clone Hammer Repository

```bash
cd /bwrcq/C/<username>
git clone git@github.com:ucb-bar/hammer.git
cd hammer
```

### Step 1: Install uv

```bash
curl -LsSf https://astral.sh/uv/install.sh | sh

# Add to PATH (add to ~/.bashrc for persistence)
export PATH="$HOME/.local/bin:$PATH"

# Verify
uv --version
# Should show: uv 0.10.x or later
```

That's it — no Python bootstrap, no pip, no wheel needed.

### Step 1.5: Set Up pg_config for psycopg2

`psycopg2` (the real PostgreSQL adapter, **not** `psycopg2-binary`) must be compiled from C source. This requires `pg_config` and the PostgreSQL development headers, which are **not** installed system-wide on BWRC servers. We extract them locally from the `libpq-devel` RPM — no root access needed.

```bash
# Create a temp directory and download the RPMs
mkdir -p /tmp/pg_build_$$
cd /tmp/pg_build_$$
dnf download --resolve libpq-devel

# Extract the devel RPM (contains pg_config + headers)
mkdir -p ~/pg_local
rpm2cpio libpq-devel-*x86_64.rpm | (cd ~/pg_local && cpio -idmv)

# Extract the libpq RPM (contains libpq.so shared libraries)
# Find the non-devel libpq RPM filename, then extract it
LIBPQ_RPM=$(ls libpq-[0-9]*x86_64.rpm 2>/dev/null | head -1)
if [ -n "$LIBPQ_RPM" ]; then
    rpm2cpio "$LIBPQ_RPM" | (cd ~/pg_local && cpio -idmv)
fi

# Create the linker symlink
ln -sf libpq.so.5 ~/pg_local/usr/lib64/libpq.so

# Go back home BEFORE cleaning up (avoids "could not identify current directory" error)
cd ~
rm -rf /tmp/pg_build_$$

# Verify pg_config works
export PATH="$HOME/pg_local/usr/bin:$PATH"
pg_config --version
# Should show: PostgreSQL 13.x

pg_config --includedir
# Should show: /bwrcq/home/<username>/pg_local/usr/include

pg_config --libdir
# Should show: /bwrcq/home/<username>/pg_local/usr/lib64
```

**Make pg_config persistent** — add to `~/.bashrc`:

```bash
echo 'export PATH="$HOME/pg_local/usr/bin:$PATH"' >> ~/.bashrc
source ~/.bashrc
```

Now `uv` (and pip) can build `psycopg2` from source whenever needed.

### Step 2: Create Virtual Environment and Install Dependencies

First, you need to convert the `pyproject.toml` from Poetry format to standard PEP 621 format. Replace the contents of `pyproject.toml` with:

```toml
[project]
name = "hammer-vlsi"
version = "0.0.0"
description = "Hammer is a physical design framework that wraps around vendor specific technologies and tools to provide a single API to create ASICs."
authors = [
    {name = "Colin Schmidt", email = "colin.schmidt@sifive.com"},
    {name = "Edward Wang", email = "edward.c.wang@compdigitec.com"},
    {name = "John Wright", email = "johnwright@eecs.berkeley.edu"},
]
maintainers = [
    {name = "Harrison Liew", email = "harrisonliew@berkeley.edu"},
    {name = "Daniel Grubb", email = "dpgrubb@berkeley.edu"},
]
readme = "README.md"
requires-python = ">=3.9"
dependencies = [
    "pydantic>=2.12.2,<3",
    "PyYAML>=6.0,<7",
    "ruamel.yaml>=0.17.21,<0.18",
    "networkx>=3.0,<4",
    "numpy>=1.23.0,<2",
    "sphinxcontrib-mermaid>=0.8.1,<0.9",
    "psycopg2>=2.9.11,<3",
    "asyncpg>=0.30.0,<1",
]

[project.optional-dependencies]
asap7-gdstk = ["gdstk>=0.9.0,<0.10"]
asap7 = ["gdspy==1.4"]

[project.scripts]
hammer-generate-mdf = "hammer.shell.hammer_generate_mdf:main"
get-config = "hammer.shell.get_config:main"
hammer-vlsi = "hammer.shell.hammer_vlsi:main"
hammer-shell-test = "hammer.shell.hammer_shell_test:main"
readlink-array = "hammer.shell.readlink_array:main"
yaml2json = "hammer.shell.yaml2json:main"
asap7_gds_scale = "hammer.technology.asap7.gds_scale:main"

[project.urls]
Repository = "https://github.com/ucb-bar/hammer"

[dependency-groups]
dev = [
    "pytest>=7.1,<8",
    "pyright>=1.1,<2",
    "types-PyYAML>=6.0.0,<7",
    "tox>=3.25.1,<4",
    "Sphinx>=5.1.1,<6",
    "sphinx-rtd-theme>=1.0.0,<2",
    "myst-parser>=0.18.0,<0.19",
    "sphinx-jsonschema>=1.19.1,<2",
    "sphinx-rtd-size>=0.2.0,<0.3",
    "pylint>=2.15,<3",
]

[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"

[tool.hatch.build.targets.wheel]
packages = ["hammer"]

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
max-line-length = 120

[tool.pylint.reports]
output-format = "text"
reports = true

[tool.pylint."messages control"]
disable = ["no-else-return", "len-as-condition"]

[tool.pyright]
exclude = ["doc/**", "e2e/**", "test_scripts/**"]
include = ["hammer", "tests"]
reportMissingImports = true
typeCheckingMode = "standard"
```

**Key differences from Poetry format**:
- `[tool.poetry]` → `[project]` (PEP 621 standard)
- `[tool.poetry.dependencies]` → `[project.dependencies]` (PEP 508 specifiers)
- `[tool.poetry.scripts]` → `[project.scripts]`
- `[tool.poetry.dev-dependencies]` → `[dependency-groups]` dev
- Build backend: `poetry-core` → `hatchling`

**Important**: Make sure you completed [Step 1.5](#step-15-set-up-pg_config-for-psycopg2) first — `pg_config` must be on your `PATH` so `psycopg2` can compile from source.

Now create the venv and install:

```bash
# Ensure pg_config is on PATH
export PATH="$HOME/pg_local/usr/bin:$HOME/.local/bin:$PATH"
pg_config --version   # Should show: PostgreSQL 13.x

# Pin Python version
uv python pin 3.11

# Create virtual environment using your Python
uv venv --python $HOME/python311/bin/python3

# Lock dependencies (psycopg2 will be built from source)
uv lock

# Install everything (project + dev dependencies)
uv sync --group dev

# Verify
source .venv/bin/activate
hammer-vlsi -h
python3 -c "import psycopg2; print('psycopg2:', psycopg2.__version__)"
python3 -c "import asyncpg; print('asyncpg:', asyncpg.__version__)"
```

**Note**: `uv sync` resolved and installed all 67+ packages in under 1 minute. Poetry typically takes 5-15 minutes for the same operation. `psycopg2` is compiled from C source during install (~20 seconds).

### Step 3: Install Apache Airflow

Apache Airflow is installed separately (not in `pyproject.toml`) using Airflow's constraint file for version compatibility:

```bash
# Make sure you're in the hammer directory with venv activated
cd /bwrcq/C/<username>/hammer
source .venv/bin/activate
export AIRFLOW_HOME=$(pwd)
export PATH="$HOME/pg_local/usr/bin:$HOME/.local/bin:$PATH"

# Define versions
AIRFLOW_VERSION=3.1.0
PYTHON_VERSION="$(python3 -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")')"
CONSTRAINT_URL="https://raw.githubusercontent.com/apache/airflow/constraints-${AIRFLOW_VERSION}/constraints-${PYTHON_VERSION}.txt"

# Remove doc packages that conflict with Airflow
pip uninstall -y myst-parser mdit-py-plugins markdown-it-py 2>/dev/null || true

# Install Airflow with constraints (uv pip is ~10x faster than pip)
uv pip install "apache-airflow==${AIRFLOW_VERSION}" --constraint "${CONSTRAINT_URL}"

# Reinstall psycopg2 from source (Airflow may have swapped it for psycopg2-binary)
uv pip install psycopg2==2.9.11 --no-binary psycopg2 --reinstall

# Verify
airflow version
# Should show: 3.1.0

python3 -c "import psycopg2; print('psycopg2:', psycopg2.__version__)"
# Should show: psycopg2: 2.9.11 (dt dec pq3 ext lo64)
```

### Step 4: Set Up PostgreSQL Connection

#### 4.1 Get PostgreSQL Password

1. Check your @berkeley.edu email for a message from the server administrator
2. Click the link and retrieve your PostgreSQL password

#### 4.2 Generate airflow.cfg

```bash
cd /bwrcq/C/<username>/hammer
source .venv/bin/activate
export AIRFLOW_HOME=$(pwd)

# This creates airflow.cfg
airflow config list > /dev/null 2>&1

# Verify it was created
ls -la airflow.cfg
```

#### 4.3 Update airflow.cfg

Edit `airflow.cfg` and update the `[database]` section:

```ini
[database]
sql_alchemy_conn = postgresql+psycopg2://<username>:YOUR_PASSWORD@barney.eecs.berkeley.edu:5433/airflow_<username>
sql_alchemy_pool_size = 10
sql_alchemy_max_overflow = 20
sql_alchemy_engine_args = {"pool_pre_ping": true}
```

Replace `<username>` with your LDAP username and `YOUR_PASSWORD` with your actual PostgreSQL password.

#### 4.4 Test Connection

```bash
airflow db check
# Should show: Connection successful.
```

### Step 5: Configure Airflow

Edit `airflow.cfg` with these critical settings:

```ini
[core]
executor = LocalExecutor
parallelism = 1
max_active_tasks_per_dag = 1
dags_folder = /bwrcq/C/<username>/hammer/dags

[api]
# Uncomment and set base_url (required for task execution in Airflow 3.x)
base_url = http://localhost:8080

[scheduler]
task_queued_timeout = 1200.0
job_heartbeat_sec = 10
scheduler_heartbeat_sec = 10
```

Create the DAGs folder:

```bash
mkdir -p dags
```

Verify configuration:

```bash
airflow config get-value database sql_alchemy_conn
airflow config get-value core executor
airflow config get-value core dags_folder
```

### Step 6: Initialize Database

```bash
cd /bwrcq/C/<username>/hammer
source .venv/bin/activate
export AIRFLOW_HOME=$(pwd)

# Run migrations
airflow db migrate

# Verify
airflow db check
airflow db check-migrations
```

### Step 7: Start Airflow

#### Option A: Standalone (Recommended)

```bash
cd /bwrcq/C/<username>/hammer
source .venv/bin/activate
export AIRFLOW_HOME=$(pwd)

airflow standalone
```

Wait for `Airflow is ready`. The auto-generated admin password is in `simple_auth_manager_passwords.json.generated`.

**Note**: In Airflow 3.x, the `airflow users create` CLI command has been removed. `airflow standalone` creates the admin user automatically.

#### Option B: Separate Processes

**Terminal 1 - Scheduler:**
```bash
cd /bwrcq/C/<username>/hammer
source .venv/bin/activate
export AIRFLOW_HOME=$(pwd)
airflow scheduler
```

**Terminal 2 - API Server:**
```bash
cd /bwrcq/C/<username>/hammer
source .venv/bin/activate
export AIRFLOW_HOME=$(pwd)
airflow api-server --port 8080
```

**Note**: In Airflow 3.x, the web UI is served by `airflow api-server`, not `airflow webserver`.

#### Access the UI

**From Remote Desktop (NoMachine/X2Go):**
- URL: http://localhost:8080
- Username: `admin`
- Password: See `simple_auth_manager_passwords.json.generated`

**Via SSH Port Forwarding (from your laptop):**
```bash
ssh -A -L 8080:localhost:8080 -J <username>@bwrcrdsl-1.eecs.berkeley.edu <username>@<compute_node>.eecs.berkeley.edu
```
Then open http://localhost:8080 in your browser.

---

## Daily Usage

### Quick Start (after initial setup)

```bash
cd /bwrcq/C/<username>/hammer
source venv.sh    # activates venv + sets AIRFLOW_HOME + adds uv to PATH
airflow standalone
```

### Convenience Script: `venv.sh`

Create this file in the hammer root:

```bash
source ./.venv/bin/activate

# Set AIRFLOW_HOME to current directory (Hammer root)
export AIRFLOW_HOME=$(pwd)

# Ensure uv and pg_config are on PATH
export PATH="$HOME/pg_local/usr/bin:$HOME/.local/bin:$PATH"

echo "Virtual environment activated."
echo "AIRFLOW_HOME set to: $AIRFLOW_HOME"
```

Usage: `source venv.sh`

---

## uv Cheat Sheet (vs Poetry)

| Task | Poetry | uv |
|------|--------|-----|
| Install tool | `curl -sSL https://install.python-poetry.org \| python3 -` | `curl -LsSf https://astral.sh/uv/install.sh \| sh` |
| Create venv | `poetry config virtualenvs.in-project true` | `uv venv` |
| Install deps | `poetry install` | `uv sync` |
| Install with dev | `poetry install` (auto) | `uv sync --group dev` |
| Add dependency | `poetry add package` | `uv add package` |
| Add dev dependency | `poetry add --dev package` | `uv add --group dev package` |
| Update lockfile | `poetry lock` | `uv lock` |
| Run command | `poetry run cmd` | `uv run cmd` |
| Activate venv | `poetry shell` | `source .venv/bin/activate` |
| Show installed | `poetry show` | `uv pip list` |
| Pin Python | N/A | `uv python pin 3.11` |
| Install Python | Need pyenv | `uv python install 3.11` |

### Key Advantages of uv

1. **10-100x faster**: Dependency resolution in milliseconds, not minutes
2. **Single binary**: No Python bootstrap required to install
3. **PEP 621 standard**: `pyproject.toml` uses the standard `[project]` format
4. **Built-in Python management**: Can install Python versions itself
5. **Lockfile**: `uv.lock` is cross-platform by default
6. **Source builds**: `--no-binary` flag compiles C extensions from source cleanly

---

## Verification

### Quick Check

```bash
cd /bwrcq/C/<username>/hammer
source venv.sh

# Test hammer
hammer-vlsi -h

# Test database
airflow db check
airflow db check-migrations

# Test postgres connection
./test_postgres_connection.sh

# Run hammer tests
pytest tests/ -m "not long" -v
```

### Verify Metadata Storage

```bash
python3 << 'EOF'
from sqlalchemy import create_engine, text
from airflow.configuration import conf

engine = create_engine(conf.get('database', 'sql_alchemy_conn'))
with engine.connect() as conn:
    result = conn.execute(text("SELECT COUNT(*) FROM dag_run"))
    print(f"DAG runs: {result.fetchone()[0]}")
    
    result = conn.execute(text("SELECT COUNT(*) FROM task_instance"))
    print(f"Task instances: {result.fetchone()[0]}")
    
    result = conn.execute(text("SELECT COUNT(*) FROM task_instance WHERE state = 'success'"))
    print(f"Successful tasks: {result.fetchone()[0]}")
EOF
```

---

## Troubleshooting

### "uv: command not found"

```bash
export PATH="$HOME/.local/bin:$PATH"
# Add to ~/.bashrc for persistence
echo 'export PATH="$HOME/.local/bin:$PATH"' >> ~/.bashrc
```

### psycopg2 fails to build from source

**Error**: `pg_config executable not found`

This means `pg_config` is not on your `PATH`. **Do NOT switch to `psycopg2-binary`**. Instead, set up `pg_config` locally (see [Step 1.5](#step-15-set-up-pg_config-for-psycopg2)):

```bash
# 1. Make sure pg_config is on PATH
export PATH="$HOME/pg_local/usr/bin:$PATH"
pg_config --version   # Should show PostgreSQL 13.x

# 2. Rebuild psycopg2 from source
uv pip install psycopg2==2.9.11 --no-binary psycopg2 --reinstall

# 3. Verify it's the real psycopg2 (compiled from C source)
python3 -c "import psycopg2; print('psycopg2:', psycopg2.__version__)"
# Should show: 2.9.11 (dt dec pq3 ext lo64)
```

**If `dnf download` doesn't work** in Step 1.5, contact:
- **Email**: anne_young@berkeley.edu
- **System Admins**: bwrc-sysadmins@lists.eecs.berkeley.edu
- **Request**: `libpq-devel` package (provides `pg_config` and PostgreSQL headers)

### Connection Refused to PostgreSQL

1. Verify you're on a compute node, not the login server
2. Check server: `ping barney.eecs.berkeley.edu`
3. Check port: `telnet barney.eecs.berkeley.edu 5433`
4. If server is down, contact: anne_young@berkeley.edu or bwrc-sysadmins@lists.eecs.berkeley.edu

### Authentication Failed

1. Verify username and password in `airflow.cfg` connection string
2. Check for extra spaces when copying password
3. Contact sysadmins if password doesn't work

### Tasks Not Starting / Network Unreachable

Ensure `base_url` is set in `[api]` section of `airflow.cfg`:
```ini
[api]
base_url = http://localhost:8080
```

### Port Already in Use

```bash
# Check what's using the port
lsof -i :8080
# Or use a different port
airflow api-server --port 8090
# Then update airflow.cfg: base_url = http://localhost:8090
```

### Migrations Pending

```bash
airflow db migrate
```

### Want to Reset Everything

```bash
cd /bwrcq/C/<username>/hammer
rm -rf .venv uv.lock airflow.cfg logs/
export PATH="$HOME/pg_local/usr/bin:$HOME/.local/bin:$PATH"
uv venv --python $HOME/python311/bin/python3
uv lock
uv sync --group dev
# Then re-do Steps 3-6
```

---

## Support

For issues specific to SledgeHammer PostgreSQL integration:
1. Check this documentation
2. Run `./test_postgres_connection.sh` for diagnostics
3. Run `./verify_postgres.sh` for database health check
4. If the PostgreSQL server is down, contact:
   - **Email**: anne_young@berkeley.edu
   - **System Admins**: bwrc-sysadmins@lists.eecs.berkeley.edu

---

**Author**: Lawrence Rhee  
**Date**: March 3, 2026  
**Airflow Version**: 3.1.0  
**uv Version**: 0.10.8+  
**PostgreSQL Version**: 13.22 (on server)  
**Python Version**: 3.11.9
