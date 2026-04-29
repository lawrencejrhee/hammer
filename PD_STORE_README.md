# Postgres PD Artifact Store (POC)

A small proof of concept that writes Hammer physical design artifacts to
Postgres, keyed by the SHA256 of their canonical JSON. Today only
`par-input.json` is stored; the goal is to validate the read/write round trip
against our existing Postgres instance before extending it to other artifacts
and to multi user setups.

## What's implemented

* `hammer/vlsi/pd_store.py`: connect, ensure schema, store, load, list, hash.
* `hammer/shell/pd_store_cli.py`: the `hammer-pd-store` CLI (`init`, `list`,
  `get`, `put`).
* `hammer-pd-store` is registered as a script entry in `pyproject.toml`.
* `syn_par_action` in `hammer/vlsi/cli_driver.py` writes `par-input.json` to
  Postgres alongside the existing on disk dump.
* Round trip verified manually against the live cluster.

The one thing not yet covered is exercising the hook inside a full syn + par
EDA run; that needs a real flow run to confirm.

## Architecture

```
syn_par_action ──► dump_config_to_json_file("par-input.json")    (existing)
      │
      └────────► pd_store.store_par_input(par_input)              (new)
                       │
                       ▼
            airflow_lawrence.hammer_poc.pd_artifacts
                       ▲
hammer-pd-store ───────┘   (init / list / get / put)
```

The table lives in its own `hammer_poc` schema so it stays isolated from the
Airflow tables that share the database.

```sql
CREATE SCHEMA IF NOT EXISTS hammer_poc;

CREATE TABLE IF NOT EXISTS hammer_poc.pd_artifacts (
    sha256      TEXT PRIMARY KEY,
    kind        TEXT NOT NULL,           -- 'par-input' for now
    top_module  TEXT,                    -- best effort, pulled from the dict
    data        JSONB NOT NULL,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
```

Writes use `INSERT ... ON CONFLICT (sha256) DO NOTHING`, so storing identical
content twice is a no op.

## Connection settings

For each field (host, port, db, user, password), the first source that
provides a value wins:

1. `HAMMER_PG_*` environment variables, if set.
2. `sql_alchemy_conn` in `airflow.cfg` (the same string Airflow uses for its
   metadata DB).
3. Hardcoded defaults.

`airflow.cfg` is located by checking `$AIRFLOW_HOME/airflow.cfg` first, then
`./airflow.cfg`, then `~/airflow/airflow.cfg`. Sourcing `venv.sh` sets
`AIRFLOW_HOME` to the Hammer root, so a working `sql_alchemy_conn` line is
usually all you need:

```
sql_alchemy_conn = postgresql+psycopg2://user:pass@host:5433/airflow_lawrence
```

The defaults if nothing else resolves:

| Field | Env var | Default |
|---|---|---|
| host | `HAMMER_PG_HOST` | `barney.eecs.berkeley.edu` |
| port | `HAMMER_PG_PORT` | `5433` |
| db   | `HAMMER_PG_DB`   | `airflow_lawrence` |
| user | `HAMMER_PG_USER` | `$USER` |
| password | `HAMMER_PG_PASSWORD` | none (raises if nothing resolves) |

## Usage

```bash
source ./venv.sh              # sets AIRFLOW_HOME, activates .venv

# One time setup (idempotent):
hammer-pd-store init

# After any syn_par run, the hook prints:
#     Stored par-input in Postgres with sha256=<hex>

hammer-pd-store list
hammer-pd-store get <sha256>
hammer-pd-store put /path/to/par-input.json     # default kind: par-input
hammer-pd-store put /path/to/blob.json --kind syn-output
```

## Files in this change

| File | Change |
|---|---|
| `hammer/vlsi/pd_store.py` | new: DB logic and SHA256 helpers |
| `hammer/shell/pd_store_cli.py` | new: `hammer-pd-store` CLI |
| `hammer/vlsi/cli_driver.py` | `syn_par_action` calls `store_par_input(par_input)` after the existing JSON dump |
| `pyproject.toml` | registers `hammer-pd-store = "hammer.shell.pd_store_cli:main"` |

## Verification

Tested against `barney.eecs.berkeley.edu:5433/airflow_lawrence` (server
reports PostgreSQL 13.23):

* `hammer-pd-store init` created `hammer_poc.pd_artifacts`.
* `hammer-pd-store put par-input.json` returned
  `3c28c437c4056771c2826ea5ca3fde2ffcadc8d356520344dfa4167dc0414917`.
* A second `put` of the same file returned the same SHA and did not create
  a duplicate row.
* `hammer-pd-store get <sha>` returned JSON that compared equal to the
  source file.
* Direct calls to `compute_sha256`, `store_par_input`, and `load_artifact`
  via `python -c` round tripped to the same SHA. Re-hashing the value
  returned by `load_artifact` produced byte identical canonical JSON, so
  nothing is being reordered or coerced through JSONB.
* `load_artifact(<missing sha>)` returns `None` rather than raising, which
  makes it safe as a cache lookup.
* `from hammer.vlsi.cli_driver import CLIDriver` still imports cleanly with
  the hook in place.
* Test rows were truncated at the end so the table starts empty for the
  next user.

## Design notes

* **Canonical JSON**: `json.dumps(..., sort_keys=True, separators=(",",":"),
  cls=HammerJSONEncoder, ensure_ascii=False)`. Logically equal dicts produce
  identical bytes, so the SHA is stable across runs and machines.
* **Non fatal hook**: the `store_par_input` call inside `syn_par_action` is
  wrapped in `try/except`. A DB outage logs a warning; it does not break the
  syn → par run.
* **Lazy import**: the hook imports `pd_store` inside the `try`, so
  `psycopg2` is not pulled in on every Hammer import path.
* **Schema isolation**: `hammer_poc` lives next to but separate from the
  Airflow schema in the same database.

## Notes on permissions and LDAP

Background reading for the upcoming LDAP integration. Cross checked against
the PostgreSQL 13 docs (the cluster runs 13.23) and against the live server
where possible.

### Mental model

Postgres splits authentication (who are you?) from authorization (what can
you do?). The two meet at a Postgres **role**. LDAP only ever touches
authentication: when an LDAP bind succeeds, Postgres still logs you in as a
role with the same name, and that role must already exist. LDAP does not
auto provision roles.

```
LDAP directory                 pg_hba.conf                    Postgres
──────────────                 ───────────                    ────────
uid=alice, ou=people, ...  →   host ... ldap ldapurl=...  →   role "alice"
                                                                 │
                                                                 ▼
                                   GRANT SELECT ON hammer_poc.pd_artifacts
                                              TO hammer_readers;
                                   GRANT hammer_readers TO alice;
                                              +
                                   (optional) CREATE POLICY ... USING (owner = current_user);
```

Authorization is then the usual `GRANT` / `REVOKE` over databases, schemas,
tables, and columns, optionally sharpened with Row Level Security policies
that filter rows by role.

### What we see on barney

Probing as `lawrencejrhee`:

| Fact | Value |
|---|---|
| Server version | PostgreSQL 13.23 |
| Our role | `lawrencejrhee` (LOGIN, INHERIT, CREATEDB; not SUPERUSER, not CREATEROLE) |
| Database | `airflow_lawrence` |
| Schema owner | `lawrencejrhee` owns `hammer_poc` and `hammer_poc.pd_artifacts` |
| RLS on table | disabled (`relrowsecurity = false`) |
| Other login roles | `andre_green`, `anne_young`, `colinpobrien04`, `connorlu`, `felicia_guo`, `ken_ho`, ... |

Two takeaways. The cluster is already multi user at the role level (one
`LOGIN` role per human), which matches the "one LDAP user, one Postgres
role" model. And we cannot `CREATE ROLE` ourselves: anything involving new
group roles or new login roles needs a DBA on barney. Our own schema and
table are ours to `GRANT` on freely.

### LDAP authentication (for reference)

> The `pg_hba.conf` snippets below are example shapes from the PostgreSQL 13
> docs, not the live config on barney. Our role cannot read
> `pg_hba_file_rules` or the file on disk, so the cluster's actual auth
> methods were not inspected for this POC. To confirm later, a superuser
> session can run:
>
> ```sql
> SHOW hba_file;
> SELECT * FROM pg_hba_file_rules;
> ```

Two LDAP modes are available:

* **Simple bind**: Postgres binds directly as
  `<ldapprefix><username><ldapsuffix>`. Suitable for flat directories or AD.

  ```
  host all all 0.0.0.0/0 ldap \
       ldapserver=ldap.eecs.berkeley.edu ldaptls=1 \
       ldapprefix="uid=" ldapsuffix=",ou=people,dc=eecs,dc=berkeley,dc=edu"
  ```

* **Search and bind**: Postgres binds with a service account
  (`ldapbinddn` + `ldapbindpasswd`), searches for the user, then re binds as
  the found DN with the client supplied password. More flexible; needed when
  users live in different OUs or can log in by either uid or email.

  ```
  host all all 0.0.0.0/0 ldap \
       ldapurl="ldap://ldap.eecs.berkeley.edu/dc=eecs,dc=berkeley,dc=edu?uid?sub" \
       ldaptls=1
  ```

Things easy to get wrong:

* The Postgres role must already exist; LDAP only validates the password.
* `ldaps://` or `ldaptls=1` only encrypts the Postgres → LDAP leg. Encrypt
  client → Postgres separately with `hostssl` entries plus server certs, or
  passwords travel in the clear.
* Mixing simple bind and search and bind options on one rule is an error.
* `pg_hba.conf` is read top to bottom; the first match wins. Stricter rules
  belong above the catch all.

### Authorization layers

Working from coarse to fine:

1. **Database**: `GRANT CONNECT ON DATABASE airflow_lawrence TO alice;` A
   role that cannot connect cannot do anything. `PUBLIC` gets `CONNECT` on
   new databases by default; revoke it for sensitive ones.
2. **Schema**: `GRANT USAGE ON SCHEMA hammer_poc TO alice;` `USAGE` is
   required just to name an object inside the schema, even with table level
   privileges already granted.
3. **Table, sequence, function**: `GRANT SELECT, INSERT ON
   hammer_poc.pd_artifacts TO alice;` Column level grants are also
   available: `GRANT SELECT (sha256, kind, created_at) ON ... TO alice;`
   lets a role list hashes without seeing the `data` payload.
4. **Row Level Security**: optional, layered on top. `ALTER TABLE ... ENABLE
   ROW LEVEL SECURITY` plus `CREATE POLICY` rules. With RLS on and no
   matching policy, the default is deny.

Two extras worth knowing:

* **Group roles** are roles without `LOGIN`. `GRANT some_group TO alice;`
  makes Alice inherit anything granted to the group. This is how dataset
  access scales: grant on objects to `hammer_project_gcd`, then add or
  remove humans from that group.
* **`DEFAULT PRIVILEGES`**: `ALTER DEFAULT PRIVILEGES IN SCHEMA hammer_poc
  GRANT SELECT ON TABLES TO hammer_readers;` covers future tables, so we
  don't have to re grant every time a new artifact table appears.

### RLS, concretely for us

If we want "Alice sees her own PD artifacts but not Bob's" inside a shared
table, RLS is the right tool. Add an `owner` column defaulting to
`current_user` and pair it with a per role visibility policy:

```sql
ALTER TABLE hammer_poc.pd_artifacts
    ADD COLUMN owner TEXT NOT NULL DEFAULT current_user;

ALTER TABLE hammer_poc.pd_artifacts ENABLE ROW LEVEL SECURITY;

CREATE POLICY pd_artifacts_owner_only ON hammer_poc.pd_artifacts
    USING       (owner = current_user)
    WITH CHECK  (owner = current_user);
```

Worth knowing from the docs:

* The table owner bypasses RLS by default. As `lawrencejrhee` (the schema
  owner) we'd see all rows unless we also run `ALTER TABLE ... FORCE ROW
  LEVEL SECURITY`. For real multi tenant data, that's what you want.
* Superusers and roles with `BYPASSRLS` always ignore policies. Don't give
  `BYPASSRLS` to humans.
* RLS enabled with no policies means deny everything. Safer than SQL's
  usual "if any privilege is granted, everything is visible".
* Policies are `PERMISSIVE` (OR'd, default) or `RESTRICTIVE` (AND'd). One
  permissive "it's mine" policy plus a restrictive "and only on TLS" policy
  composes both checks.
* Referential integrity checks bypass RLS. If foreign keys cross artifact
  tables, hidden rows can leak through uniqueness errors.

### Suggested pattern for the LDAP integration

Assuming the eventual story is "every human has an LDAP account, mapped to
a Postgres login role, with dataset level isolation":

1. **Auth**: a DBA adds an `ldap` line to `pg_hba.conf` (search and bind,
   `ldaptls=1`) pointing at EECS LDAP, plus `hostssl` for client TLS.
2. **Roles**: the DBA runs `CREATE ROLE <uid> LOGIN;` for each onboarded
   user. This is already the pattern on barney. Pointing
   `ldapsearchattribute` at `uid` keeps LDAP and Postgres identities in
   sync by name.
3. **Groups per dataset / project**: the DBA creates `hammer_readers`,
   `hammer_writers`, `hammer_project_gcd`, etc., and grants them to users.
4. **Object ACLs**: we (the schema owner) manage what each group can do.

   ```sql
   REVOKE ALL ON SCHEMA hammer_poc FROM PUBLIC;
   GRANT  USAGE ON SCHEMA hammer_poc TO hammer_readers, hammer_writers;
   GRANT  SELECT ON hammer_poc.pd_artifacts TO hammer_readers;
   GRANT  SELECT, INSERT ON hammer_poc.pd_artifacts TO hammer_writers;
   ALTER  DEFAULT PRIVILEGES IN SCHEMA hammer_poc
          GRANT SELECT ON TABLES TO hammer_readers;
   ```

5. **Row isolation when needed**: add an `owner` column, enable RLS, and
   `FORCE ROW LEVEL SECURITY` so the schema owner is filtered too.
6. **Password handling in the client**: stop hard coding a password env
   var. Two reasonable options: rely on `~/.pgpass` (psycopg2 honors it
   automatically) so each human supplies their own credential, or use
   Kerberos / GSSAPI if EECS LDAP is Kerberos backed (your OS login is
   your DB identity, no password over the wire).

### Open issues for this POC

* The POC currently runs as `lawrencejrhee` (the schema owner) with no ACLs
  configured. Any role with `CONNECT` on `airflow_lawrence` and `USAGE` on
  `hammer_poc` could already read the table. Before real data lands, run at
  minimum:

  ```sql
  REVOKE ALL ON SCHEMA hammer_poc FROM PUBLIC;
  REVOKE ALL ON hammer_poc.pd_artifacts FROM PUBLIC;
  ```

* We can't create roles ourselves (no `CREATEROLE`). Group roles and per
  user roles need a DBA with admin on barney.
* We can't read `pg_hba_file_rules` from our role, so the application side
  can't see which auth method each user gets today. The LDAP snippets above
  are illustrative shapes from the PostgreSQL 13 docs, not the real rules
  on barney. When a DBA is available, ask for the output of `SHOW
  hba_file;` and the relevant lines, and replace this section with reality.
* The password resolves from either `HAMMER_PG_PASSWORD` or
  `sql_alchemy_conn` in `airflow.cfg`. `airflow.cfg` is currently tracked
  in git with the password in plaintext, which has to change before any
  sensitive data lands. Move the password into a gitignored env file or
  `~/.pgpass`, and scrub `airflow.cfg`.

## Future work

* Storing other artifacts: `syn-output.json`, `par-output-full.json`, logs,
  GDS, bitstreams.
* A richer hash that incorporates tool versions, environment, and inputs.
  Today's SHA is purely over the JSON content.
* Multi user ACLs, migrations tooling, async driver usage, connection
  pooling.
* Rebuilding on disk build directories from the DB, which is the next
  milestone.
