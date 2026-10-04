from hammer.vlsi import pd_store


def test_group_members_can_use_the_ledger_sequences() -> None:
    ddl = pd_store._DDL
    block = ddl[ddl.index(f"WHERE rolname = '{pd_store.SLEDGEHAMMER_GROUP}'"):]
    schema, group = pd_store.SCHEMA_NAME, pd_store.SLEDGEHAMMER_GROUP
    assert f"GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA {schema} TO {group}" in block
    assert f"ALTER DEFAULT PRIVILEGES IN SCHEMA {schema} GRANT USAGE, SELECT ON SEQUENCES TO {group}" in block
