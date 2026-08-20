# SledgeHammer command reference

Every console command the venv installs, what it is for, and the flags that
matter. Two commands are SledgeHammer's own (`sledgehammer`, `studio`); the
rest are Hammer's, unchanged.

Everything here works from a terminal. The web UI is optional — there is no
step in a normal flow that requires it.

| Command | Purpose |
|---|---|
| **`sledgehammer`** | Launch the Airflow stack; run flows from the CLI; Airflow passthrough |
| **`studio`** | Admin + cache CLI for the shared PD database (43 subcommands) |
| `hammer-vlsi` | Hammer's flow driver (syn, par, drc, lvs, build, ...) |
| `get-config` | Print a resolved config key |
| `hammer-generate-mdf` | Generate a macro definition file |
| `hammer-shell-test` | Shell-integration smoke test |
| `readlink-array`, `yaml2json`, `asap7_gds_scale` | Small utilities |

## sledgehammer

### Running flows (no GUI)

The flags are Hammer's, so a Makefile switches over by changing the binary.

```bash
# generate the DAG if missing, trigger, stream to the terminal, exit with the run's status
sledgehammer run syn par -e env.yml -p design.yml --obj_dir build/ChipTop

# the DAG is already registered: -e/-p are only needed to (re)generate it
sledgehammer run drc lvs --obj_dir build/ChipTop

# fire and forget
sledgehammer run syn --obj_dir build/ChipTop --no-wait
```

Stages: `sim_rtl power_rtl syn sim_syn timing_syn formal_syn power_syn par
sim_par timing_par formal_par power_par drc lvs` (dashes accepted).

| Flag | Meaning |
|---|---|
| `-e`, `-p` | environment / project configs, repeatable, same as hammer |
| `--obj_dir` | build directory (required) |
| `--design` | design name; defaults to the obj_dir basename |
| `--module M` | restrict to these modules, repeatable (hierarchical flows) |
| `--redo` | ignore dependency checks for this run |
| `--local` | skip the DB cache pull, run the tool locally |
| `--from-step` / `--to-step` / `--only-step` | sub-step control |
| `--steps-stage {syn,par}` | which stage the step flags apply to |
| `--workspace`, `--project` | workspace routing, ledger project label |
| `--run-id` | name the run instead of an auto `cli_<epoch>` |
| `--regen` | regenerate the DAG even if one is registered |
| `--no-wait` | trigger and return immediately |

The exit code follows the run, so `sledgehammer run ... && next-step` behaves.
Ctrl-C detaches from the stream; the run keeps going.

### Reading results

```bash
sledgehammer status --design ChipTop     # latest run, per-task states
sledgehammer status --design ChipTop --run-id lvsfinal2_1787205495
sledgehammer runs --design ChipTop -n 5  # recent runs
```

### Stack and passthrough

```bash
sledgehammer                  # launch with LDAP + TOTP (sets SLEDGE_2FA=1)
sledgehammer standalone       # same
SLEDGE_2FA=0 sledgehammer     # plain LDAP, no second factor
sledgehammer db migrate       # any airflow subcommand, secrets pre-loaded
sledgehammer dags list
SLEDGE_DRYRUN=1 sledgehammer  # print what it would run
```

The launcher decrypts the GPG secrets and pins `AIRFLOW_HOME` first, so
Airflow reaches the metadata DB with nothing exported by hand. The flow
commands (`run`, `status`, `runs`) instead *respect* an `AIRFLOW_HOME` you
already set, so they compose with an existing stack.

## studio

### Time saved — `time-saved` (the paper numbers)

```bash
studio time-saved                          # headline totals
studio time-saved --group-by design        # per design
studio time-saved --design ChipTop --since 2026-08-03
studio time-saved --csv tat.csv --group-by project
studio time-saved --cache-only             # exclude legacy-equivalent skips
```

Parallel-flow savings are measured from Airflow task windows. When the ledger
spans more than one Airflow instance (after a migration or a merge), point at
the others or every run recorded elsewhere scores as sequential:

```bash
export SLEDGE_EXTRA_METADATA_CONNS="postgresql://user:pw@host:5433/airflow_a,postgresql://user:pw@host:5433/airflow_b"
```

### Cache and artifacts

`blob-list` `blob-find` `blob-delete` `blob-reassign` `stage-key` `stage-push`
`stage-pull` `cache-status`

```bash
studio cache-status                     # is the cache and ledger on and reachable
studio blob-list -n 20
studio blob-delete --design gcd --dry-run
```

The cache is **off unless enabled** — `HAMMER_PD_CACHE=1` or
`vlsi.pd_cache.enabled`. A run that records nothing usually means this.

### Checkpoints

`checkpoints` `checkpoints-push` `checkpoints-fetch` `checkpoints-clear`

Sub-step checkpoints stream to the database while syn and par run, so a killed
task loses at most one push interval. Tune with
`HAMMER_CHECKPOINT_STREAM_SECS` (default 300); `HAMMER_CHECKPOINT_STREAM=0`
turns streaming off.

### Master database

`master-push` `master-pull` `master-list`, plus the older artifact verbs
`list` `get` `put`

### Users and access

`onboard` `grant` `revoke` `whitelist` `2fa` `admin`

### Workspaces

`workspace-list` `workspace-show` `workspace-set` `workspace-unset`

```bash
studio workspace-set alice /scratch/alice/build
```

Runs never create workspace rows; an unregistered user is an error naming the
fix, not a silent default.

### Notifications

`notify-email` `smtp-setup` `notify-test`

```bash
studio smtp-setup --user berkeley.sledgehammer.studio@gmail.com --password-stdin
studio notify-email lawrencejrhee "a@berkeley.edu, b@gmail.com"
studio notify-test
```

Completion mail is sent by a task in the DAG, not an Airflow callback —
Airflow 3.1 serializes DAGs without their callbacks, so a DAG-level callback
never fires.

### Design setup and housekeeping

`design-register` `augment` `make-dag` · `init` `reap` `project-set`
`cache-events-clear` `wipe-blobs` `wipe-master` `wipe-artifacts` `wipe-all`

```bash
studio init            # create the schema on a fresh database
studio reap            # list EDA processes left by dead runs (--kill to end them)
```

## Related documents

Setup lives in its own files: [DATABASE_SETUP.md](DATABASE_SETUP.md) (an
existing Postgres), [LOCAL_POSTGRES_SETUP.md](LOCAL_POSTGRES_SETUP.md)
(standing one up), [WORKSPACE_SETUP.md](WORKSPACE_SETUP.md),
[AIRFLOW_LDAP_SETUP.md](AIRFLOW_LDAP_SETUP.md),
[AIRFLOW_2FA_SETUP.md](AIRFLOW_2FA_SETUP.md), [SECRETS.md](SECRETS.md),
[TIME_SAVED_TRACKER.md](TIME_SAVED_TRACKER.md),
[SUBSTEP_RESUME.md](SUBSTEP_RESUME.md).

## Coming from legacy Hammer

Same flags, same target names. The differences are that stages compose in one
command, modules run in parallel, and the bridges between stages are DAG edges
instead of things you type.

### Running a flow

| Legacy Hammer | SledgeHammer |
|---|---|
| `make syn` | `sledgehammer syn` |
| `make par` (chains syn) | `sledgehammer par` |
| `make all` | `sledgehammer run syn par` |
| `make drc` then `make lvs` | `sledgehammer run drc lvs` *(parallel)* |
| `make syn-RocketTile` | `sledgehammer syn-RocketTile` |
| `make par-RocketTile` | `sledgehammer par-RocketTile` |
| `make redo-syn-RocketTile` | `sledgehammer redo-syn-RocketTile` |
| `make build/Top/hammer.d` | *(implicit — generated on first run)* |
| `make hier-par-to-syn-Top` | *(implicit — a DAG edge)* |
| `make par-A; make par-B; ...` (7×, serial) | `sledgehammer par` (all leaves, parallel) |

Raw driver form, if you prefer it over the Makefile:

```bash
# legacy: every config re-passed, one action, blocking
./iris-vlsi -e env.yml -p pdks/techname.yml -p design.yml -p io/iris-bumps.yml \
    -p specs/constr/iris-rockettile.yml ... -p build/IrisVLSITop/inputs.yml \
    --obj_dir build/IrisVLSITop par

# sledgehammer: configs only when generating; afterwards the DAG remembers
sledgehammer run par -e env.yml -p design-dag.yml --obj_dir build/IrisVLSITop
sledgehammer par -t IrisVLSITop        # every later run
```

### Flags

Identical to `hammer-vlsi` unless noted.

| Flag | Legacy | SledgeHammer |
|---|---|---|
| `-e`, `-p` | yes | yes (only needed to generate the DAG) |
| `--obj_dir` | yes | optional — read from the registered DAG |
| `-t`, `--top` | yes | yes (`--design` also accepted) |
| `--force` | yes | yes (`--redo` also accepted) |
| `--local` | yes | yes |
| `--start_before_step` / `--from_step` | yes | yes |
| `--stop_after_step` / `--to_step` | yes | yes |
| `--only_step` | yes | yes |
| `--module M` | n/a (separate target per module) | restrict to modules, repeatable |
| `--workspace`, `--project` | n/a | workspace routing, ledger label |
| `--no-wait`, `--run-id`, `--regen` | n/a | trigger-and-return, name a run, force regeneration |
| `--syn_rundir` etc. | yes | not mirrored — the DAG derives rundirs per module |
| `-v`, `-f`, `-o` | yes | not mirrored — inputs and outputs flow along DAG edges |

### Where obj_dir comes from

Legacy took it from the Makefile, so you had to `cd vlsi` first. A generated
DAG has `OBJ_DIR` baked in, so running one needs neither:

1. `--obj_dir` when given
2. `-t <top>` → read out of that DAG
3. bare command → the DAG whose `OBJ_DIR` is under the current directory
4. `$OBJ_DIR`, then the Makefile — only when nothing is registered yet and the
   DAG is about to be generated

### Actions with no legacy equivalent

```bash
sledgehammer status --design Top        # per-task state of the latest run
sledgehammer runs --design Top          # run history
sledgehammer run par --from_step route_opt_design   # resume mid-stage
studio time-saved                       # what the flow saved vs a make-style rerun
```

Substep resume (a killed par restarts from its last tool-confirmed checkpoint)
and the shared cache (a colleague's identical syn restores in seconds) have no
legacy counterpart at all.
