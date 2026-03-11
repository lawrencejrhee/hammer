# Part 2: Adding Chipyard to Your Hammer + Airflow + PostgreSQL Setup

> **Prerequisite**: Complete **all of [UV_SETUP.md](UV_SETUP.md)** first (Parts 1 through Step 7). You should have:
> - Hammer cloned and installed with uv
> - A working virtual environment (`.venv/`) with `hammer-vlsi`, `psycopg2`, and Airflow 3.1.0
> - A PostgreSQL database created and connected
> - `airflow standalone` running successfully on `http://localhost:8080`
>
> This document adds Chipyard on top of that and configures the Sledgehammer DAGs so you can run real VLSI flows (GCD, RocketTile) through the Airflow UI.

## Table of Contents

- [Overview](#overview)
- [Step 1: Clone Chipyard](#step-1-clone-chipyard)
- [Step 2: Chipyard Build Setup](#step-2-chipyard-build-setup)
  - [2.1 Install Miniforge (Required for build-setup.sh)](#21-install-miniforge-required-for-build-setupsh)
  - [2.2 Activate Conda](#22-activate-conda)
  - [2.3 Run Chipyard Build Setup](#23-run-chipyard-build-setup)
  - [2.4 Install Hammer PDK Plugins (Optional)](#24-install-hammer-pdk-plugins-optional)
  - [2.5 When You Don't Need Any of This](#25-when-you-dont-need-any-of-this)
  - [2.6 After Build: Go Back to the uv Virtual Environment](#26-after-build-go-back-to-the-uv-virtual-environment)
- [Step 3: Modify Hammer Files for Airflow Compatibility](#step-3-modify-hammer-files-for-airflow-compatibility)
  - [3.1 Remove sys.exit() from cli_driver.py](#31-remove-sysexit-from-cli_driverpy)
  - [3.2 Add run_cli_driver() wrapper to hammer_vlsi.py](#32-add-run_cli_driver-wrapper-to-hammer_vlsipy)
  - [3.3 Add get_param() helper for DAG parameter access](#33-add-get_param-helper-for-dag-parameter-access)
  - [3.4 Fix the DAG flow so clean doesn't block the pipeline](#34-fix-the-dag-flow-so-clean-doesnt-block-the-pipeline)
  - [3.5 Remove sys.exit(0) from exit_ task](#35-remove-sysexit0-from-exit_-task)
  - [3.6 Remove unused import_task_to_dag import](#36-remove-unused-import_task_to_dag-import)
  - [3.7 Comment out QRC tech file lines in Genus (RHEL 9 workaround)](#37-comment-out-qrc-tech-file-lines-in-genus-rhel-9-workaround)
  - [3.8 Set DAG parameter defaults to True](#38-set-dag-parameter-defaults-to-true)
- [Step 4: Update e2e Config Files with Absolute Paths](#step-4-update-e2e-config-files-with-absolute-paths)
- [Step 5: Copy Sky130 PDK Files](#step-5-copy-sky130-pdk-files)
- [Step 6: Update Airflow Configuration for DAGs](#step-6-update-airflow-configuration-for-dags)
  - [6.1 Point dags_folder to hammer/shell](#61-point-dags_folder-to-hammershell)
  - [6.2 Add Security Keys (CRITICAL)](#62-add-security-keys-critical)
  - [6.3 JWT Secret for API Authentication (ALSO CRITICAL)](#63-jwt-secret-for-api-authentication-also-critical)
  - [6.4 Verify Configuration](#64-verify-configuration)
  - [6.5 Run DB Migrate](#65-run-db-migrate)
- [Step 7: Verify DAG Discovery](#step-7-verify-dag-discovery)
- [Available DAGs](#available-dags)
- [Usage](#usage)
- [Clearing / Resetting a DAG Run](#clearing--resetting-a-dag-run)
- [Troubleshooting](#troubleshooting)
- [All Modifications Summary](#all-modifications-summary)
- [Directory Structure](#directory-structure)
- [Best Practices](#best-practices)

---

## Overview

After completing UV_SETUP.md, your directory looks like this:

```
/bwrcq/C/<username>/hammer/          # AIRFLOW_HOME
├── airflow.cfg                      # Airflow config (PostgreSQL connected, executor=LocalExecutor)
├── pyproject.toml                   # PEP 621 (converted from Poetry for uv)
├── uv.lock
├── .venv/                           # Virtual environment with Hammer + Airflow + psycopg2
├── venv.sh                          # Convenience activation script
├── hammer/                          # Hammer Python package
├── e2e/                             # End-to-end test configs
└── dags/                            # Empty placeholder DAGs folder
```

We're going to:
1. **Clone Chipyard** into the hammer directory
2. **Modify Hammer source files** so they work inside Airflow workers (no `sys.exit()`, proper parameter handling, etc.)
3. **Copy PDK files** needed for the GCD demo
4. **Reconfigure Airflow** to point `dags_folder` at `hammer/shell/` (where the real DAG file lives) and add security keys
5. **Verify** the DAGs show up and can be triggered

---

## Step 1: Clone Chipyard

```bash
cd /bwrcq/C/<username>/hammer
source venv.sh

git clone git@github.com:ucb-bar/chipyard.git chipyard
```

Add `chipyard/` to `.gitignore` (it's a separate git repository):

```bash
echo "chipyard/" >> .gitignore
```

---

## Step 2: Chipyard Build Setup

Chipyard's `build-setup.sh` requires **conda** to set up its environment. Since the uv-based setup (UV_SETUP.md) does not install conda, you need to install a local Miniforge first. This is a one-time step — conda is only needed for chipyard's build process, not for daily Airflow use.

### 2.1 Install Miniforge (Required for build-setup.sh)

Chipyard's `build-setup.sh` Step 1 creates a conda environment. It needs the `conda` command to be available. Install Miniforge into your workspace:

```bash
cd /bwrcq/C/<username>/hammer

# Download Miniforge installer
wget -O Miniforge3.sh \
  "https://github.com/conda-forge/miniforge/releases/latest/download/Miniforge3-$(uname)-$(uname -m).sh"

# Install into workspace (non-interactive: -b for batch, -p for prefix path)
bash Miniforge3.sh -b -p "$(pwd)/conda"

# Clean up installer
rm Miniforge3.sh
```

### 2.2 Activate Conda

```bash
cd /bwrcq/C/<username>/hammer

# Activate your local conda
eval "$($(pwd)/conda/bin/conda shell.bash hook)"
conda activate

# Verify
conda --version
# Should show: conda 24.x.x
```

> **Important**: Make sure all conda-related settings in `~/.bashrc` are **commented out** before this step. Having a different conda auto-activate on login can conflict with the local Miniforge.

### 2.3 Run Chipyard Build Setup

```bash
cd /bwrcq/C/<username>/hammer/chipyard

# Source BWRC environment for EDA tools (VCS, Verdi, license servers)
source /tools/C/ee290-sp25/bwrc-env.sh

# Run build setup (skip FireSim steps 6-9 — not needed for VLSI flows)
./build-setup.sh riscv-tools -s 6 -s 7 -s 8 -s 9
```

This will:
- Create a conda environment at `chipyard/.conda-env/` (~5-10 minutes)
- Initialize chipyard submodules (~5-10 minutes)
- Build RISC-V toolchain collateral (Spike, PK, tests) (~10-20 minutes)
- Install CIRCT

**Total time**: 30-60 minutes depending on network and CPU.

> **If this fails with `conda: command not found`**: You forgot to activate conda in Step 2.2. Run `eval "$($(pwd)/../conda/bin/conda shell.bash hook)"` and try again.

### 2.4 Install Hammer PDK Plugins (Optional)

If you need Intel or TSMC PDK support (e.g., for `Sledgehammer_demo_rocket` with intech22):

```bash
cd /bwrcq/C/<username>/hammer/chipyard/vlsi

# Intel intech22 plugin
git submodule add git@bwrcrepo.eecs.berkeley.edu:intech22/hammer-intech22-plugin.git
pip install -e hammer-intech22-plugin

# TSMC tstech16c plugin
git submodule add git@bwrcrepo.eecs.berkeley.edu:tstech16/hammer-tstech16c-plugin.git
pip install -e hammer-tstech16c-plugin
```

> **Note**: These require access to the BWRC git repository (`bwrcrepo.eecs.berkeley.edu`). If you get permission denied, contact bwrc-sysadmins@lists.eecs.berkeley.edu to request access.

### 2.5 When You Don't Need Any of This

If you **only** need the GCD demo (`Sledgehammer_demo_gcd`):
- **Skip Steps 2.1–2.4 entirely**
- The GCD DAG uses Hammer's built-in `e2e/` test configs with the Sky130 PDK
- It does not require a built chipyard, conda, or PDK plugins
- The chipyard clone from Step 1 is sufficient

### 2.6 After Build: Go Back to the uv Virtual Environment

After `build-setup.sh` completes, you're in chipyard's conda environment. For Airflow and Hammer, switch back to the uv virtual environment:

```bash
# Deactivate conda (if active)
conda deactivate

# Go back to hammer root and activate the uv venv
cd /bwrcq/C/<username>/hammer
source venv.sh
```

**For daily use, you never need conda again.** The `source venv.sh` command is all you need. Conda was only required for the one-time `build-setup.sh` step.
---

## Step 3: Modify Hammer Files for Airflow Compatibility

Several Hammer source files need modification to work correctly within Airflow. These changes are **required** — without them, tasks will crash the Airflow worker process or fail to execute.

### 3.1 Remove `sys.exit()` from `cli_driver.py`

**File**: `hammer/vlsi/cli_driver.py` (at the bottom of the file, around line ~1725-1727)

**Problem**: `CLIDriver.main()` calls `sys.exit()` at the end of execution (line ~1727). This terminates the entire Python process, which in Airflow's case is the worker — causing the task to be marked as `failed` even if the VLSI step succeeded.

**Fix**: Replace `sys.exit()` calls with `return` statements:

```python
# Line ~1725: Change from:
#   sys.exit(1)
# To:
    return 1

# Line ~1727: Change from:
#   sys.exit(self.run_main_parsed(vars(parser.parse_args(args))))
# To:
    return self.run_main_parsed(vars(parser.parse_args(args)))
```

This allows `CLIDriver.main()` to complete and return control to the Airflow task function instead of killing the process.

### 3.2 Add `run_cli_driver()` wrapper to `hammer_vlsi.py`

**File**: `hammer/shell/hammer_vlsi.py`

**Problem**: Even after fixing `cli_driver.py`, some code paths in `CLIDriver` or its dependencies may still call `sys.exit()`. We need a safety net.

**What to do**: The stock `hammer_vlsi.py` is a 9-line script that just calls `CLIDriver().main()`. Replace it entirely with the Sledgehammer DAG definitions. The modified file includes a wrapper function at the top (after imports):

```python
def run_cli_driver():
    """Wrapper to call CLIDriver().main() safely within Airflow.
    CLIDriver calls sys.exit() which would kill the Airflow worker process.
    We catch SystemExit and only raise an error for non-zero exit codes."""
    try:
        CLIDriver().main()
    except SystemExit as e:
        if e.code != 0 and e.code is not None:
            raise RuntimeError(f"CLIDriver.main() failed with exit code {e.code}")
        # Exit code 0 means success - just return normally
```

Then replace **all** calls to `CLIDriver().main()` in the `AIRFlow` class methods (`build`, `sim_rtl`, `syn`, `par`) with `run_cli_driver()`.

> **WARNING**: Do NOT use find-and-replace-all blindly. The `run_cli_driver()` function itself calls `CLIDriver().main()` internally — if you replace that too, you get infinite recursion (`RecursionError: maximum recursion depth exceeded`). Only replace `CLIDriver().main()` in the `AIRFlow` class methods and **not** inside `run_cli_driver` itself.

**Important**: Update the absolute paths in the `AIRFlow` and `AIRFlow_rocket` classes to match your installation:

```python
# In AIRFlow.__init__():
self.vlsi_dir = '/bwrcq/C/<username>/hammer/e2e'  # ← your path

# In AIRFlow_rocket.__init__():
self.vlsi_dir = '/bwrcq/C/<username>/hammer/e2e'   # ← your path
self.specs_abs = '/bwrcq/C/<username>/hammer/specs' # ← your path
```

> **Tip**: You can copy the modified `hammer_vlsi.py` from a reference setup (e.g., `/bwrcq/home/lawrencejrhee/hammer_uv/hammer/shell/hammer_vlsi.py`) and only update the absolute paths.

### 3.3 Add `get_param()` helper for DAG parameter access

**Problem**: When triggering a DAG from the Airflow UI without passing explicit config, `context['dag_run'].conf` is an empty dict `{}`. Code like `context['dag_run'].conf.get('build', False)` returns `False` — ignoring the DAG's `Param` defaults entirely. This causes all tasks to be skipped.

**Fix**: Add a helper function inside each DAG definition function:

```python
def get_param(context, param_name, default=True):
    """Get parameter value from conf (runtime) or params (default), with fallback"""
    # Check runtime conf first (explicit override)
    if param_name in context.get('dag_run', {}).conf:
        return context['dag_run'].conf[param_name]
    # Check params (DAG defaults)
    if param_name in context.get('params', {}):
        return context['params'][param_name]
    # Fallback to default
    return default
```

Use this in **all** task and decider functions instead of reading `dag_run.conf` directly.

### 3.4 Fix the DAG flow so `clean` doesn't block the pipeline

**Problem**: The original DAG had `start` branch to either `clean` or `build_decider`. If `start` chose `clean`, the flow went `clean → exit_`, skipping all other steps (build, sim, syn, par).

**Fix**: Restructure so `clean` is a pass-through step that always leads to `build_decider`:

```
start → clean → build_decider → build → sim_or_syn_decide → ...
  │                                              │
  └→ exit_ (only if no steps enabled)            ├→ sim_rtl → exit_
                                                  └→ syn_decider → syn → par_decider → par → exit_
```

- `start` now routes to `clean` (if any step is enabled) or `exit_` (if nothing is enabled)
- `clean` executes the clean action if `clean=True`, otherwise passes through silently
- `clean` **always flows to `build_decider`**, not to `exit_`
- `build_decider` then routes to `build`, `sim_or_syn_decide`, or `exit_`

### 3.5 Remove `sys.exit(0)` from `exit_` task

**Problem**: The `exit_` task in the original DAG called `sys.exit(0)`, which kills the Airflow worker.

**Fix**: Remove it — the task just prints and returns:

```python
@task(trigger_rule=TriggerRule.NONE_FAILED)
def exit_():
    """Exit task"""
    print("Exiting")
    # Do NOT call sys.exit(0) here
```

### 3.6 Remove unused `import_task_to_dag` import

**Problem**: The original DAG file imported `from hammer.vlsi.cli_driver import import_task_to_dag`. This function exists only in Isabelle's version of `cli_driver.py`, not in the standard Hammer repository. Since the import resolves against whichever Hammer is installed in the Python environment, it may import from the wrong `cli_driver.py` and fail.

**Fix**: Remove the import line. The function is not used anywhere in the DAG code.

### 3.7 Comment out QRC tech file lines in Genus (RHEL 9 workaround)

**File**: `hammer/synthesis/genus/__init__.py` (around line 231)

**Problem**: Cadence Genus fails with `error while loading shared libraries: libnsl.so.1` on RHEL 9 systems. This is a system-level issue — `libnsl` was removed in RHEL 9.

**Workaround**: Comment out lines ~231–233:

```python
# Commented out QRC tech file setting - may cause issues if QRC is needed in the future
# if len(qrc_files) > 0:
#     verbose_append("set_db qrc_tech_file {{ {files} }}".format(
#         files=qrc_files[0]
#     ))
```

> **Note**: This is a temporary workaround. If QRC extraction is needed for a specific PDK, you will need to find or install the missing `libnsl.so.1` library. Contact sysadmins if needed.

### 3.8 Set DAG parameter defaults to `True`

**Problem**: The original DAG had all step parameters (`build`, `sim_rtl`, `syn`, `par`) defaulting to `False`. This meant triggering the DAG without explicit config would skip everything.

**Fix**: Change defaults to `True` so all steps run by default:

```python
params={
    'clean': Param(default=False, ...),    # Clean stays False by default
    'build': Param(default=True, ...),     # Changed from False
    'sim_rtl': Param(default=True, ...),   # Changed from False
    'syn': Param(default=True, ...),       # Changed from False
    'par': Param(default=True, ...),       # Changed from False
}
```

---

## Step 4: Update e2e Config Files with Absolute Paths

Airflow workers do not execute from the `e2e/` directory, so all source file references must use **absolute paths**. Without this, tasks will fail with `FileNotFoundError`.

**File**: `e2e/configs-design/gcd/common.yml`

```yaml
synthesis.inputs:
  top_module: "gcd"
  input_files:
    - "/bwrcq/C/<username>/hammer/e2e/src/gcd.v"
```

**File**: `e2e/configs-design/gcd/sim-rtl.yml`

```yaml
sim.inputs:
  level: "rtl"
  input_files:
    - "/bwrcq/C/<username>/hammer/e2e/src/gcd.v"
    - "/bwrcq/C/<username>/hammer/e2e/src/gcd_tb.v"
```

> **Note**: All YAML config files that reference source files (`*.v`, `*.sv`) must use absolute paths. Check `syn.yml`, `par.yml`, and any other design config for relative paths and replace them.

---

## Step 5: Copy Sky130 PDK Files

If using the `sky130` PDK for the GCD demo, copy PDK files from a reference setup:

```bash
SRC=/bwrcq/C/bandk1451/chipyard-sledgehammer-br-airflow3/chipyard/vlsi/hammer/hammer/technology/sky130
DST=/bwrcq/C/<username>/hammer/hammer/technology/sky130

cp $SRC/__init__.py $DST/__init__.py
cp $SRC/sram-cache.json $DST/sram-cache.json
mkdir -p $DST/sram_compiler
cp $SRC/sram_compiler/__init__.py $DST/sram_compiler/__init__.py

# Verify
ls $DST/__init__.py $DST/sram-cache.json $DST/sram_compiler/__init__.py
```

---

## Step 6: Update Airflow Configuration for DAGs

Now that the DAGs are defined in `hammer/shell/hammer_vlsi.py`, update `airflow.cfg` to point Airflow at the right directory and add the security keys that Airflow 3.x requires.

### 6.1 Point `dags_folder` to hammer/shell

```ini
[core]
# IMPORTANT: Point to the hammer/shell directory, where hammer_vlsi.py (the DAG file) lives.
# NOT hammer/e2e/dags/ — that directory is empty and irrelevant.
dags_folder = /bwrcq/C/<username>/hammer/hammer/shell
```

> **This is the #1 reason DAGs don't show up.** If `dags_folder` points to the wrong directory, `airflow dags list` returns nothing and `airflow dags list-import-errors` returns "No data found" — because Airflow isn't even scanning the right folder. Verify with: `airflow config get-value core dags_folder`

### 6.2 Add Security Keys (CRITICAL)

**These are essential for Airflow 3.x.** Without them, the scheduler cannot communicate with the API server and **all tasks will fail** with:

```
ServerResponseError: Invalid auth token: Signature verification failed
```

Generate keys:

```bash
# Generate Fernet key
python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"

# Generate secret key
python -c "import secrets; import base64; print(base64.b64encode(secrets.token_bytes(16)).decode())"
```

Set them in `airflow.cfg`:

```ini
[core]
fernet_key = <your_generated_fernet_key>
internal_api_secret_key = <your_generated_secret_key>

[api]
secret_key = <same_secret_key_as_internal_api_secret_key>
```

> **IMPORTANT**: `internal_api_secret_key` and `secret_key` **must be the same value**. If they don't match, tasks will crash immediately with signature verification errors. This was the most common cause of "tasks fail instantly with empty logs" during setup.

### 6.3 JWT Secret for API Authentication (ALSO CRITICAL)

In Airflow 3.x, the scheduler and executor authenticate with the API server using **JWT tokens**. Even if `fernet_key`, `internal_api_secret_key`, and `secret_key` are all set correctly, tasks will **still** fail with `Invalid auth token: Signature verification failed` if the `[api_auth]` section is missing.

Generate a JWT secret:

```bash
python -c "import secrets; import base64; print(base64.b64encode(secrets.token_bytes(16)).decode())"
```

Add this section to `airflow.cfg`:

```ini
[api_auth]
jwt_secret = <your_generated_jwt_secret>
jwt_expiration_time = 86400
jwt_cli_expiration_time = 3600
```

> **Why is this separate from `secret_key`?** Airflow 3.x uses a dedicated JWT signing mechanism for API authentication between internal components (scheduler ↔ API server ↔ executor). The `secret_key` is used for session cookies and CSRF, while `jwt_secret` is specifically used to sign and verify the JWT tokens that internal components exchange. If `jwt_secret` is missing, Airflow falls back to a default or generates a random one per process — causing the scheduler's tokens to be rejected by the API server.
>
> **This is the #1 most subtle auth issue in Airflow 3.x.** The `fernet_key` and `secret_key` errors are well-documented, but the `jwt_secret` requirement is easy to miss because it's in a separate `[api_auth]` section that doesn't exist in the default generated config.

### 6.4 Verify Configuration

```bash
source venv.sh

airflow config get-value core dags_folder
# Should show: /bwrcq/C/<username>/hammer/hammer/shell

airflow config get-value database sql_alchemy_conn
airflow config get-value core executor
airflow config get-value core fernet_key
airflow config get-value api base_url
```

### 6.5 Run DB Migrate

After changing `airflow.cfg`, re-run migrations:

```bash
source venv.sh
airflow db migrate
```

#### Why `airflow db migrate` (not `airflow db init`)?

In Airflow 2.x, the command was `airflow db init`. **Airflow 3.x removed `db init` entirely** and replaced it with `airflow db migrate`. This isn't just a rename — it reflects a better design:

| | `airflow db init` (Airflow 2.x, removed) | `airflow db migrate` (Airflow 3.x) |
|---|---|---|
| **How it works** | Created the full schema from scratch | Uses Alembic migrations — only applies pending changes |
| **Idempotent?** | No — could overwrite things | Yes — safe to run any number of times |
| **On fresh DB** | Creates everything | Creates everything (same result) |
| **On existing DB** | Risky — could reset state | Only applies what's new, never destroys data |
| **On version upgrade** | Required separate `db upgrade` | `migrate` handles both creation and upgrades |

**You have no choice** — `airflow db init` simply doesn't exist in Airflow 3.x. But `migrate` is the better command anyway.

---

## Step 7: Verify DAG Discovery

```bash
source venv.sh

# Reserialize DAGs (forces Airflow to re-scan the dags_folder)
airflow dags reserialize

# List DAGs — look for the Sledgehammer DAGs
airflow dags list | grep Sledgehammer
# Should show:
# Sledgehammer_demo_gcd
# Sledgehammer_demo_rocket

# Check for import errors (should be empty)
airflow dags list-import-errors
# Should show: No data found
```

**If DAGs don't appear:**

1. **Verify `dags_folder`**: `airflow config get-value core dags_folder` — must point to `hammer/shell/`
2. **Check import errors**: `airflow dags list-import-errors` — if empty, Airflow isn't scanning the right folder
3. **Test the import manually**: `python3 hammer/shell/hammer_vlsi.py` — see if Python can parse it
4. **Verify Hammer is importable**: `python3 -c "from hammer.vlsi import CLIDriver; print('OK')"` — if this fails, re-run `uv pip install -e .`

### Verify tables were created in PostgreSQL

```bash
export PGPASSWORD='<your_password>'
psql -h barney.eecs.berkeley.edu -p 5433 -U <your_username> \
  -d airflow_<your_username> -c "\dt" | head -20
# Should show tables like: dag, dag_run, task_instance, job, log, xcom, etc.
```

Now start Airflow:

```bash
source venv.sh
airflow standalone
```

---

## Available DAGs

### Sledgehammer_demo_gcd

GCD (Greatest Common Divisor) design workflow.

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `clean` | boolean | `False` | Clean the build directory |
| `build` | boolean | `True` | Run the build step |
| `sim_rtl` | boolean | `True` | Run RTL simulation |
| `syn` | boolean | `True` | Run logic synthesis |
| `par` | boolean | `True` | Run place and route |

**Default config**: design=`gcd`, PDK=`sky130`, tools=`cm`, env=`bwrc`

### Sledgehammer_demo_rocket

RocketTile design workflow (includes SRAM generation step).

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `clean` | boolean | `False` | Clean the build directory |
| `build` | boolean | `True` | Run the build step |
| `sim_rtl` | boolean | `True` | Run RTL simulation |
| `sram_generator` | boolean | `True` | Generate SRAM macros |
| `syn` | boolean | `True` | Run logic synthesis |
| `par` | boolean | `True` | Run place and route |

**Default config**: design=`intel2x2`, PDK=`intech22`, tools=`cm`, env=`intel2x2`

### DAG Task Flow

Both DAGs use a branching pattern where "decider" tasks route to the correct step:

```
start → clean → build_decider ──→ build → sim_or_syn_decide ──→ sim_rtl → exit_
                     │                            │
                     │                            └──→ syn_decider → syn → par_decider → par → exit_
                     │
                     └──→ exit_ (if no steps enabled)
```

The Rocket DAG adds `sram_decider → sram_generator` between `sim_or_syn_decide` and `syn_decider`.

### IMPORTANT: `sim_rtl` and `syn/par` are mutually exclusive

The `sim_or_syn_decide` branch checks `sim_rtl` **first**. If `sim_rtl=True`, it takes the simulation path and **skips** synthesis/PAR entirely. If you want to run synthesis and PAR, you must set `sim_rtl=False`:

```python
# Inside sim_or_syn_decide:
if get_param(context, 'sim_rtl', True):
    return 'sim_rtl'               # ← Takes this path if sim_rtl is True
elif get_param(context, 'syn', True) or get_param(context, 'par', True):
    return 'syn_decider'           # ← Only reaches here if sim_rtl is False
return 'exit_'
```

**Examples:**
- Default trigger (no config): `sim_rtl=True` → runs `build → sim_rtl → exit_` (syn/par skipped)
- `{"sim_rtl": false, "syn": true, "par": true}`: runs `build → syn → par → exit_`
- `{"sim_rtl": false}`: runs only `build → exit_` (sim_rtl disabled, syn/par default True but depends on sim_or_syn_decide logic)

---

## Usage

### Triggering DAGs via CLI

Always set the environment first:

```bash
source venv.sh
```

Then trigger:

```bash
# GCD - Full default flow (build + sim_rtl)
airflow dags trigger Sledgehammer_demo_gcd

# GCD - Build + Synthesis + PAR (skip sim)
airflow dags trigger Sledgehammer_demo_gcd --conf '{"sim_rtl": false}'

# GCD - Build only
airflow dags trigger Sledgehammer_demo_gcd --conf '{"sim_rtl": false, "syn": false, "par": false}'

# GCD - Full flow with clean
airflow dags trigger Sledgehammer_demo_gcd --conf '{"clean": true}'

# Rocket - Build + SRAM + Synthesis
airflow dags trigger Sledgehammer_demo_rocket --conf '{"sim_rtl": false}'
```

### Triggering via Web UI

1. Open browser to `http://localhost:<port>`
2. Find your DAG in the list
3. Unpause the DAG (toggle the switch)
4. Click the **Play** button (▶)
5. (Optional) Enter JSON config to override defaults:
   ```json
   {"sim_rtl": false, "syn": true, "par": true}
   ```
6. Click "Trigger"

### Checking Status

```bash
source venv.sh

# List DAGs
airflow dags list

# Check import errors
airflow dags list-import-errors

# Check task states for a run
airflow tasks states-for-dag-run Sledgehammer_demo_gcd "<run_id>"
```

---

## Clearing / Resetting a DAG Run

### Clear all runs for a DAG

```bash
source venv.sh
airflow tasks clear Sledgehammer_demo_gcd --yes
```

### Clear a specific DAG run

```bash
airflow tasks clear Sledgehammer_demo_gcd \
  --start-date "2026-02-11" --end-date "2026-02-13" --yes
```

### Delete all DAG run history (fresh start)

```bash
airflow dags delete Sledgehammer_demo_gcd --yes
# The DAG will be re-imported on the next scheduler scan
```

> **Note**: `dags delete` removes the DAG and its history from the metadata database. The DAG file itself is not deleted. On the next scheduler scan, Airflow will re-import the DAG from the file.

### Via Web UI

1. Go to the DAG's Grid view
2. Click on the specific DAG run
3. Click "Clear" to reset all tasks in that run
4. This allows you to re-trigger the same run

---

## Troubleshooting

### DAGs not appearing (the most common issue)

**Symptom**: `airflow dags list | grep Sledgehammer` returns nothing. `airflow dags list-import-errors` returns "No data found".

**Cause**: `dags_folder` in `airflow.cfg` is pointing to the wrong directory. Airflow isn't even scanning where `hammer_vlsi.py` lives.

**Fix**:
```bash
# 1. Check where Airflow is looking
airflow config get-value core dags_folder

# 2. It MUST be:
#    /bwrcq/C/<username>/hammer/hammer/shell
#    NOT hammer/e2e/dags/ or ~/airflow/dags/ or anything else

# 3. Fix in airflow.cfg:
#    [core]
#    dags_folder = /bwrcq/C/<username>/hammer/hammer/shell

# 4. Force re-scan
airflow dags reserialize
```

### Tasks fail with "Invalid auth token: Signature verification failed"

**Cause**: One or more of the following security keys in `airflow.cfg` are missing or inconsistent:
1. `[core] fernet_key` — encryption key
2. `[core] internal_api_secret_key` — internal API signing key
3. `[api] secret_key` — must match `internal_api_secret_key`
4. `[api_auth] jwt_secret` — JWT token signing key (**most commonly missed**)

This is the **#1 most common runtime issue** with Airflow 3.x.

**Fix (check in order)**:
1. Ensure `fernet_key`, `internal_api_secret_key`, and `secret_key` are all set (see [Step 6.2](#62-add-security-keys-critical))
2. Ensure `internal_api_secret_key` and `secret_key` are the **same value**
3. **If the error persists after steps 1-2**, add the `[api_auth]` section with `jwt_secret` (see [Step 6.3](#63-jwt-secret-for-api-authentication-also-critical))

**Prevention**: Always ensure these 4 keys are present in `airflow.cfg`:
```ini
[core]
fernet_key = <key>
internal_api_secret_key = <key>

[api]
secret_key = <same_as_internal_api_secret_key>

[api_auth]
jwt_secret = <key>
```

**Alternative fix**: Copy a full working `airflow.cfg` from an existing installation and change only `dags_folder` and `sql_alchemy_conn`.

### Tasks fail with "state attribute is queued" / state mismatch

**Cause**: Port mismatch — `[api] base_url` doesn't match the actual port the API server is running on.

**Fix**: Ensure `[api] base_url` port matches the actual running port. For `airflow standalone`, the default is 8080.

### Tasks fail with empty logs

**Cause**: Usually the auth token issue (see above). The task process crashes before it can write any logs.

**Fix**: Check the scheduler log:
```bash
grep "Signature\|auth token" $AIRFLOW_HOME/logs/scheduler/*.log
```

### Tasks fail with "hammer-shell-test returned non-zero exit status" or "No module named 'hammer'"

**Cause**: The `hammer` Python package is not discoverable. This typically happens when the editable install (`.pth` file) points to a **non-existent path** (e.g., a stale path from a previous install location).

**Diagnosis**:
```bash
cat $(python -c "import site; print(site.getsitepackages()[0])")/hammer_vlsi.pth
```
If this prints a path that doesn't exist on disk, that's the problem.

**Fix**:
```bash
cd /bwrcq/C/<username>/hammer
uv pip install -e .

# Verify
hammer-shell-test
# Should print: "hammer-shell appears to be on the path"

cd /tmp && python -c "from hammer.vlsi import CLIDriver; print('OK')"
```

**Also**: Source the BWRC environment before starting Airflow for EDA tool access:
```bash
source /tools/C/ee290-sp25/bwrc-env.sh
```

### Tasks crash with `RecursionError: maximum recursion depth exceeded`

**Cause**: The `run_cli_driver()` wrapper function is calling itself recursively instead of calling `CLIDriver().main()`. This happens if you use find-and-replace-all to replace `CLIDriver().main()` with `run_cli_driver()` — it also replaces the call inside the wrapper itself.

**Fix**: Ensure `run_cli_driver()` calls `CLIDriver().main()` internally:
```python
def run_cli_driver():
    try:
        CLIDriver().main()          # ← Must be CLIDriver().main(), NOT run_cli_driver()
    except SystemExit as e:
        if e.code != 0 and e.code is not None:
            raise RuntimeError(f"CLIDriver.main() failed with exit code {e.code}")
```

### Tasks always skip (only start, clean, exit_ run)

**Cause 1**: DAG parameter defaults are `False`. Triggering without config means all step parameters are `False`.
**Fix**: Change parameter defaults to `True` in the DAG definition (see [Step 3.8](#38-set-dag-parameter-defaults-to-true)).

**Cause 2**: Task code reads from `context['dag_run'].conf.get('param', False)` instead of using `get_param()`. When no config is passed, `conf` is an empty dict and `.get()` returns the hardcoded `False` default, ignoring the DAG's `Param` defaults.
**Fix**: Use the `get_param()` helper (see [Step 3.3](#33-add-get_param-helper-for-dag-parameter-access)).

**Cause 3**: The `clean` task was routing to `exit_` instead of continuing the pipeline.
**Fix**: Restructure the DAG flow so `clean` leads to `build_decider` (see [Step 3.4](#34-fix-the-dag-flow-so-clean-doesnt-block-the-pipeline)).

### `syn`, `par` tasks are skipped even though they're set to True

**This is expected behavior.** The `sim_or_syn_decide` branch treats `sim_rtl` and `syn/par` as **mutually exclusive paths**. If `sim_rtl=True` (which it is by default), the DAG takes the simulation path and skips synthesis/PAR.

**To run syn/par**: Trigger with `sim_rtl=False`:
```bash
airflow dags trigger Sledgehammer_demo_gcd --conf '{"sim_rtl": false}'
```

### ImportError: cannot import name 'import_task_to_dag'

**Cause**: The DAG file tries to import `import_task_to_dag` from `hammer.vlsi.cli_driver`. This function exists in Isabelle's version of cli_driver but not in the standard Hammer repo.

**Fix**: Remove the import line from `hammer_vlsi.py` — the function is not used.

### `genus: error while loading shared libraries: libnsl.so.1`

**Cause**: RHEL 9 removed `libnsl.so.1` which Cadence Genus depends on.

**Workaround**: Comment out lines 231-233 in `hammer/synthesis/genus/__init__.py` (see [Step 3.7](#37-comment-out-qrc-tech-file-lines-in-genus-rhel-9-workaround)).

**Proper fix**: Install `libnsl` on the system (requires root):
```bash
sudo dnf install libnsl
```
If you don't have root access, contact bwrc-sysadmins@lists.eecs.berkeley.edu.

### `airflow db init` doesn't work

Airflow 3.x removed `db init` entirely. Use `airflow db migrate` instead. See [Step 6.5](#65-run-db-migrate) for the full explanation.

### Port Already in Use

```bash
# Find process using port
ss -tlnp | grep <port>
# Or: lsof -i :<port>

# Kill it
kill -9 <PID>

# Or pick a different port and update airflow.cfg [api] base_url
```

---

## All Modifications Summary

This section lists **every file that was modified** from the stock Hammer/Chipyard repositories, why it was changed, and what was changed.

### Files Modified

| File | Change | Why |
|------|--------|-----|
| `pyproject.toml` | Converted from Poetry to PEP 621 for uv | uv compatibility (done in UV_SETUP.md) |
| `airflow.cfg` | Set `dags_folder` to `hammer/shell/`; added `[api_auth] jwt_secret`; set security keys; set PostgreSQL connection | Airflow configuration for VLSI DAGs and auth |
| `hammer/vlsi/cli_driver.py` | Replaced `sys.exit(1)` → `return 1`; replaced `sys.exit(self.run_main_parsed(...))` → `return self.run_main_parsed(...)` | Prevent `sys.exit()` from killing Airflow workers |
| `hammer/shell/hammer_vlsi.py` | Added `run_cli_driver()` wrapper; added `get_param()` helper; changed param defaults to `True`; restructured `clean` flow; removed unused import; removed `sys.exit(0)` from `exit_` task; set absolute paths in `AIRFlow.__init__` | Make DAGs work correctly in Airflow |
| `hammer/synthesis/genus/__init__.py` | Commented out lines 231–233 (QRC tech file setting) | Workaround for missing `libnsl.so.1` on RHEL 9 |
| `hammer/technology/sky130/__init__.py` | Copied from reference setup | Ensure sky130 PDK files are present |
| `hammer/technology/sky130/sram-cache.json` | Copied from reference setup | Ensure sky130 PDK files are present |
| `hammer/technology/sky130/sram_compiler/__init__.py` | Copied from reference setup | Ensure sky130 PDK files are present |
| `e2e/configs-design/gcd/common.yml` | Relative paths → absolute paths | Airflow workers don't run from e2e/ |
| `e2e/configs-design/gcd/sim-rtl.yml` | Relative paths → absolute paths | Airflow workers don't run from e2e/ |
| `.gitignore` | Added chipyard/, conda/, airflow.cfg, logs/, wheels/ | Git cleanliness |
| `venv.sh` | Created convenience script | Easy environment activation (done in UV_SETUP.md) |

### Files NOT Modified (left as-is from stock Hammer)

| File | Note |
|------|------|
| `e2e/configs-design/gcd/syn.yml` | Must have proper synthesis configuration |
| `e2e/configs-design/gcd/par.yml` | Must have proper PAR configuration |
| `e2e/configs-design/gcd/sky130.yml` | PDK-specific design config |
| `e2e/configs-env/bwrc-env.yml` | BWRC environment config |
| `e2e/configs-pdk/sky130.yml` | Sky130 PDK config |
| `e2e/configs-tool/cm.yml` | Commercial tools config |

---

## Directory Structure

After completing both UV_SETUP.md and this guide:

```
hammer/                               # AIRFLOW_HOME
├── airflow.cfg                       # Airflow configuration (CRITICAL)
├── pyproject.toml                    # PEP 621 project config (for uv)
├── uv.lock                           # uv lockfile
├── .venv/                            # Virtual environment
├── venv.sh                           # Convenience activation script
├── conda/                            # Miniforge (for chipyard build-setup.sh only)
├── chipyard/                         # Chipyard repository (cloned in Step 1)
├── logs/                             # Airflow task logs
│   ├── scheduler/                    # Scheduler logs (check for errors)
│   └── dag_id=.../                   # Per-DAG task logs
├── simple_auth_manager_passwords...  # Auto-generated admin password
├── hammer/                           # Hammer Python package
│   ├── shell/
│   │   └── hammer_vlsi.py            # ★ DAG definitions (Sledgehammer_demo_gcd, _rocket)
│   ├── vlsi/
│   │   └── cli_driver.py             # ★ CLIDriver (modified: sys.exit → return)
│   ├── synthesis/
│   │   └── genus/
│   │       └── __init__.py           # ★ Genus plugin (modified: QRC workaround)
│   └── technology/
│       └── sky130/                   # ★ Sky130 PDK files (copied from reference)
└── e2e/                              # End-to-end configs
    ├── configs-env/                  # Environment configs (bwrc-env.yml)
    ├── configs-pdk/                  # PDK configs (sky130.yml)
    ├── configs-tool/                 # Tool configs (cm.yml)
    └── configs-design/               # Design configs
        └── gcd/
            ├── common.yml            # ★ Design config (absolute paths)
            ├── sim-rtl.yml           # ★ Simulation config (absolute paths)
            ├── syn.yml               # Synthesis config
            ├── par.yml               # PAR config
            └── sky130.yml            # PDK-specific design config
```

Files marked with ★ were modified from the stock Hammer repository.

---

## Best Practices

1. **All 4 security keys**: Always set `fernet_key`, `internal_api_secret_key`, `secret_key`, AND `jwt_secret` before starting Airflow (see [Step 6.2](#62-add-security-keys-critical) and [Step 6.3](#63-jwt-secret-for-api-authentication-also-critical))
2. **Port consistency**: `[api] base_url` must match the actual port Airflow runs on
3. **Single scheduler**: Ensure only one scheduler instance is running against a database
4. **Regular backups**: `pg_dump -h host -p port -U user dbname > backup.sql`
5. **Source BWRC env**: Always source the EDA tool environment before starting Airflow
6. **Set AIRFLOW_HOME**: Every terminal/script that runs `airflow` commands must have `AIRFLOW_HOME` set
7. **No `sys.exit()` in tasks**: Never use `sys.exit()` in Airflow task code — always use `return` or raise exceptions
8. **Wrap `CLIDriver().main()`**: Always use the `run_cli_driver()` wrapper, never call `CLIDriver().main()` directly from tasks
9. **Verify editable installs after moving repos**: If you move or clone the repository to a new path, re-run `uv pip install -e .` from the hammer directory so the `.pth` file points to the correct location
10. **Use `airflow db migrate` freely**: It's idempotent — safe to run anytime, only applies pending changes
11. **Use absolute paths in YAML configs**: Airflow workers don't run from the e2e directory, so relative paths will fail
12. **Understand `sim_rtl` vs `syn/par` exclusivity**: The DAG branches — you can run sim OR syn/par, not both in one run

---

## References

- [UV_SETUP.md](UV_SETUP.md) — Part 1: Hammer + uv + Airflow + PostgreSQL installation
- [Chipyard Documentation](https://chipyard.readthedocs.io/)
- [Hammer VLSI Documentation](https://hammer-vlsi.readthedocs.io/)
- [Apache Airflow 3.x Documentation](https://airflow.apache.org/docs/)
- [uv Documentation](https://docs.astral.sh/uv/)
- [PostgreSQL Documentation](https://www.postgresql.org/docs/)
- [psycopg2 Documentation](https://www.psycopg.org/docs/)

---

**Author**: Lawrence Rhee
**Date**: March 10, 2026
**Airflow Version**: 3.1.0
**uv Version**: 0.10.8+
**PostgreSQL Version**: 13.22 (on server)
**Python Version**: 3.11.9
