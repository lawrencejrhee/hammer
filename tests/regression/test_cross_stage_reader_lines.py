import ast
import importlib
import inspect
import json
import os

from hammer.vlsi import fingerprints as fp


def _file(path, text="x\n"):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)
    return path


def _roots(obj):
    return fp.make_roots(str(obj / "tech-cache"), str(obj), hammer_root="")


def _line(path, obj):
    return fp.file_line(str(path), _roots(obj))


def test_netgen_lvs_sees_the_magic_rcfile_binary_and_tech_file(tmp_path):
    obj = tmp_path / "obj"
    rcfile = _file(tmp_path / "pdk" / "magic" / "t.magicrc")
    techfile = _file(tmp_path / "pdk" / "magic" / "t.tech")
    magic = _file(tmp_path / "bin" / "magic")
    db = {"vlsi.core.lvs_tool": "hammer.lvs.netgen", "drc.magic.rcfile": str(rcfile),
          "drc.magic.magic_bin": str(magic)}
    lvs = fp.reader_lines(db, "lvs", _roots(obj))["lvs"]
    assert lvs == sorted([f"KEY:drc.magic.magic_bin={json.dumps(str(magic))}",
                          f"KEY:drc.magic.rcfile={json.dumps(str(rcfile))}",
                          _line(rcfile, obj), _line(techfile, obj), _line(magic, obj)])
    techfile.write_text("changed\n")
    assert fp.reader_lines(db, "lvs", _roots(obj))["lvs"] != lvs
    other = _file(tmp_path / "pdk2" / "t.magicrc")
    assert fp.reader_lines(dict(db, **{"drc.magic.rcfile": str(other)}), "lvs", _roots(obj))["lvs"] != lvs
    del db["drc.magic.magic_bin"]
    assert "KEY:drc.magic.magic_bin=<absent>" in fp.reader_lines(db, "lvs", _roots(obj))["lvs"]
    db["vlsi.core.lvs_tool"] = "hammer.lvs.pegasus"
    assert fp.reader_lines(db, "lvs", _roots(obj))["lvs"] == []
    assert fp.reader_lines(db, "drc", _roots(obj))["drc"] == []


def test_genus_reads_the_innovus_binary_only_in_a_physical_flow(tmp_path):
    obj = tmp_path / "obj"
    innovus = _file(tmp_path / "cadence" / "innovus")
    db = {"vlsi.core.synthesis_tool": "hammer.synthesis.genus", "par.innovus.innovus_bin": str(innovus)}
    ilm_absent = f"KEY:{fp.ILM_OUTPUT_KEY}=<absent>"
    assert fp.reader_lines(db, "synthesis", _roots(obj))["synthesis"] == [ilm_absent]
    db["synthesis.genus.phys_flow_effort"] = "Medium"
    physical = fp.reader_lines(db, "synthesis", _roots(obj))["synthesis"]
    assert physical == sorted([ilm_absent, f"KEY:par.innovus.innovus_bin={json.dumps(str(innovus))}",
                               _line(innovus, obj)])
    innovus.write_text("reinstalled, same size?\n")
    assert fp.reader_lines(db, "synthesis", _roots(obj))["synthesis"] != physical
    db["synthesis.genus.phys_flow_effort"] = "none"
    assert fp.reader_lines(db, "synthesis", _roots(obj))["synthesis"] == [ilm_absent]
    db["vlsi.core.synthesis_tool"] = "hammer.synthesis.yosys"
    assert fp.reader_lines(db, "synthesis", _roots(obj))["synthesis"] == []


def test_vivado_par_reads_the_synthesis_vivado_settings_and_dirs(tmp_path):
    obj = tmp_path / "obj"
    setup = _file(tmp_path / "xilinx" / "settings64.sh")
    board = _file(tmp_path / "boards" / "arty" / "board.xml")
    db = {"vlsi.core.par_tool": "hammer.par.vivado", "synthesis.vivado.setup_script": str(setup),
          "synthesis.vivado.board_files": str(tmp_path / "boards"), "synthesis.vivado.part_fpga": "xc7a35t",
          "synthesis.yosys.ignored": "x"}
    par = fp.reader_lines(db, "par", _roots(obj))["par"]
    assert par == sorted([f"KEY:synthesis.vivado.board_files={json.dumps(str(tmp_path / 'boards'))}",
                          'KEY:synthesis.vivado.part_fpga="xc7a35t"',
                          f"KEY:synthesis.vivado.setup_script={json.dumps(str(setup))}",
                          _line(setup, obj), _line(board, obj)])
    db["vlsi.core.synthesis_tool"] = "hammer.synthesis.vivado"
    assert fp.reader_lines(db, "synthesis", _roots(obj))["synthesis"] == [_line(board, obj)]
    db["vlsi.core.par_tool"] = "hammer.par.innovus"
    assert fp.reader_lines(db, "par", _roots(obj))["par"] == [f"KEY:{fp.ILM_OUTPUT_KEY}=<absent>"]


def _ilms(obj, module="Child"):
    base = obj / f"par-{module}"
    files = {n: _file(base / n, f"{n} of {module}\n") for n in ("Child.lef", "Child.gds", "Child.v", "Child.sdc")}
    _file(base / "ChildILMDir" / "mmmc" / "ilm_data" / "Child" / "a.tcl")
    _file(base / "ChildILMDir" / "mmmc" / "ilm_data" / "Child" / "b.v.gz")
    value = [{"dir": str(base / "ChildILMDir"), "data_dir": str(base / "ChildILMDir" / "mmmc" / "ilm_data"),
              "module": module, "lef": str(files["Child.lef"]), "gds": str(files["Child.gds"]),
              "netlist": str(files["Child.v"]), "sdcs": [str(files["Child.sdc"])]}]
    return value, files


def test_input_ilms_are_content_hashed_into_the_reading_stage(tmp_path):
    obj_a, obj_b = tmp_path / "objA", tmp_path / "elsewhere" / "objB"
    value_a, files_a = _ilms(obj_a)
    value_b, _ = _ilms(obj_b)
    db = {"vlsi.core.synthesis_tool": "hammer.synthesis.genus", fp.ILM_OUTPUT_KEY: value_a}
    a = fp.reader_lines(db, "synthesis", _roots(obj_a))["synthesis"]
    b = fp.reader_lines(dict(db, **{fp.ILM_OUTPUT_KEY: value_b}), "synthesis", _roots(obj_b))["synthesis"]
    assert a == b
    assert f"<OBJ_DIR>/par-Child/Child.lef:sha256=" in "\n".join(a)
    assert any(line.startswith("<OBJ_DIR>/par-Child/ChildILMDir/mmmc/ilm_data/Child/b.v.gz:sha256=") for line in a)
    key = [line for line in a if line.startswith("KEY:")]
    assert key == ["KEY:" + fp.ILM_OUTPUT_KEY + "=" + json.dumps([{
        "data_dir": "<OBJ_DIR>/par-Child/ChildILMDir/mmmc/ilm_data", "dir": "<OBJ_DIR>/par-Child/ChildILMDir",
        "gds": "<OBJ_DIR>/par-Child/Child.gds", "lef": "<OBJ_DIR>/par-Child/Child.lef", "module": "Child",
        "netlist": "<OBJ_DIR>/par-Child/Child.v", "sdcs": ["<OBJ_DIR>/par-Child/Child.sdc"]}], sort_keys=True)]
    files_a["Child.lef"].write_text("rewritten\n")
    assert fp.reader_lines(db, "synthesis", _roots(obj_a))["synthesis"] != a
    assert fp.reader_lines(dict(db, **{"vlsi.core.lvs_tool": "hammer.lvs.pegasus"}), "lvs", _roots(obj_a)) == {
        "lvs": []}
    assert fp.reader_lines(dict(db, **{"vlsi.core.drc_tool": "hammer.drc.pegasus"}), "drc", _roots(obj_a)) == {
        "drc": []}


def test_ilms_in_the_own_rundir_are_left_out(tmp_path):
    obj = tmp_path / "obj"
    value, _ = _ilms(obj)
    db = {"vlsi.core.par_tool": "hammer.par.innovus", fp.ILM_OUTPUT_KEY: value}
    own = fp.reader_lines(db, "par", _roots(obj), own_rundir=str(obj / "par-Child"))["par"]
    assert own == [line for line in own if line.startswith("KEY:")] and len(own) == 1
    assert len(fp.reader_lines(db, "par", _roots(obj), own_rundir=str(obj / "par-rundir"))["par"]) > 1


def test_icv_include_dirs_are_walked(tmp_path):
    obj = tmp_path / "obj"
    inc = _file(tmp_path / "icv" / "inc" / "rules.rh")
    nested = _file(tmp_path / "icv" / "inc" / "sub" / "more.rh")
    db = {"vlsi.core.drc_tool": "hammer.drc.icv", "drc.icv.include_dirs": [str(tmp_path / "icv" / "inc"), "rel"]}
    assert fp.reader_lines(db, "drc", _roots(obj))["drc"] == sorted([_line(inc, obj), _line(nested, obj)])
    assert fp.reader_lines(db, None, _roots(obj))["lvs"] == []


def test_reader_lines_reject_unknown_stages():
    try:
        fp.reader_lines({}, "sram_generator")
    except ValueError:
        return
    raise AssertionError("sram_generator is not an owned stage tag")


def _covered(stage, tool, key):
    return any(r.stage == stage and tool in r.tools
               and any(key == k or (k.endswith(".") and key.startswith(k)) for k in r.keys)
               for r in fp._READERS)


def _trees(directory):
    for dirpath, dirnames, filenames in os.walk(directory):
        dirnames[:] = [d for d in dirnames if d != "__pycache__"]
        for name in sorted(filenames):
            if name.endswith(".py"):
                with open(os.path.join(dirpath, name), encoding="utf-8") as f:
                    yield ast.parse(f.read())


def _tool_packages():
    hammer = fp.hammer_dir()
    for stage in fp.OWNED_STAGE_TAGS:
        base = os.path.join(hammer, stage)
        for tool in sorted(os.listdir(base)) if os.path.isdir(base) else []:
            if os.path.isfile(os.path.join(base, tool, "__init__.py")):
                yield stage, tool, list(_trees(os.path.join(base, tool)))


def _common_packages():
    base = os.path.join(fp.hammer_dir(), "common")
    return {name: list(_trees(os.path.join(base, name))) for name in sorted(os.listdir(base))
            if os.path.isdir(os.path.join(base, name)) and name != "__pycache__"}


def _imported(trees):
    names = set()
    for tree in trees:
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module:
                names.add(node.module)
                names.update(f"{node.module}.{a.name}" for a in node.names)
            elif isinstance(node, ast.Import):
                names.update(a.name for a in node.names)
    return names


def _uses(trees, pkg):
    return any(n == f"hammer.common.{pkg}" or n.startswith(f"hammer.common.{pkg}.") for n in _imported(trees))


def _calls(trees):
    for tree in trees:
        for node in ast.walk(tree):
            if isinstance(node, ast.Call):
                func = node.func
                name = func.attr if isinstance(func, ast.Attribute) else getattr(func, "id", None)
                if name:
                    yield name, node


def _literal_key(node):
    arg = node.args[0] if node.args else None
    if isinstance(arg, ast.JoinedStr) and arg.values:
        arg = arg.values[0]
    if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
        return arg.value
    return None


def _accessor(name, node):
    if name in ("get_setting", "get_setting_suffix", "get_setting_type", "has_setting", "has_setting_type"):
        return True
    owner = node.func.value if isinstance(node.func, ast.Attribute) else None
    owner_name = getattr(owner, "attr", None) or getattr(owner, "id", None) or ""
    return name == "get" and owner_name.endswith("database")


def _full_tree_default(stage, tool):
    cls = importlib.import_module(f"hammer.{stage}.{tool}").tool
    return inspect.signature(cls.get_input_ilms).parameters["full_tree"].default


def _reads_ilms(node, default):
    full_tree = [kw.value for kw in node.keywords if kw.arg == "full_tree"] + node.args[:1]
    if not full_tree:
        return default is not True
    return not any(isinstance(v, ast.Constant) and v.value is True for v in full_tree)


def test_cross_stage_readers_table_is_complete():
    common = _common_packages()
    reads = set()
    for stage, tool, trees in _tool_packages():
        used = trees + [t for pkg, pkg_trees in common.items() if _uses(trees, pkg) for t in pkg_trees]
        for name, node in _calls(used):
            key = _literal_key(node) if _accessor(name, node) else None
            if key is not None and fp.owner(key) not in (fp.GLOBAL_SCOPE, stage):
                reads.add((stage, tool, key))
    assert {("lvs", "netgen", "drc.magic.rcfile"), ("synthesis", "genus", "par.innovus.innovus_bin"),
            ("par", "vivado", "synthesis.vivado.binary"), ("par", "vivado", "synthesis.vivado.dcp_macro_dir")} <= reads
    assert sorted(r for r in reads if not _covered(*r)) == []
    for reader in fp._READERS:
        for key in reader.keys:
            if key != fp.ILM_OUTPUT_KEY:
                assert any(s == reader.stage and t in reader.tools and (k == key or k.startswith(key))
                           for s, t, k in reads), (reader, key)


def _ilm_methods(trees, default):
    names = set()
    for tree in trees:
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                if any(name == "get_input_ilms" and _reads_ilms(call, default) for name, call in _calls([node])):
                    names.add(node.name)
    return names


def test_every_ilm_reading_tool_is_listed():
    common = [tree for trees in _common_packages().values() for tree in trees]
    assert {"generate_mmmc_script", "process_reg_paths"} <= _ilm_methods(common, False)
    readers, listed = set(), set()
    for stage, tool, trees in _tool_packages():
        default = _full_tree_default(stage, tool)
        methods = _ilm_methods(common, default)
        if any((name == "get_input_ilms" and _reads_ilms(node, default)) or name in methods
               for name, node in _calls(trees)):
            readers.add((stage, tool))
        if _covered(stage, tool, fp.ILM_OUTPUT_KEY):
            listed.add((stage, tool))
    assert {("synthesis", "genus"), ("synthesis", "innovus_plus"), ("par", "innovus"), ("timing", "tempus")} <= readers
    assert _full_tree_default("lvs", "netgen") is True and not any(stage == "lvs" for stage, _ in readers)
    assert sorted(readers ^ listed) == []
