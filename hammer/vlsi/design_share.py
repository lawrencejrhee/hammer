"""Design sharing: package a design so a partner can build it in a directory of their own and hit its cache.

A bundle holds what a build reads from the owner's checkout: the env and project configs the owner's
DAG runs with, the driver script, and every file or directory those configs name under the working
directory (where make and the DAG run) or directly in the build dir. Paths are stored relative to those
two roots. Anything outside them -- the PDK, tool installs, site-wide configs -- is left out and must
exist at the same absolute path on the partner's machine (BWRC shares one filesystem); the manifest
lists those paths so a pull can say which are missing.

The cache keys name files under the working directory by a portable token (fingerprints.make_roots), so
a partner who pulls the bundle into a directory of their own and builds from there computes the owner's
keys: syn and par restore the owner's results from the PD cache instead of running the tools.

The database decides who may pull what (pd_store's row-level security on the bundle and share tables);
this module only packs, unpacks and rebuilds.
"""
from __future__ import annotations

import ast
import contextlib
import datetime
import getpass
import gzip
import hashlib
import io
import json
import os
import re
import subprocess
import sys
import tarfile
import zlib
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, Iterator, List, Optional, Tuple

from hammer.vlsi import fingerprints

BUNDLE_VERSION = 1
STATE_FILE = ".sledgehammer-pull.json"
BUILD_CONFIG = ".sledgehammer-pull-build.yml"
MAX_BUNDLE_FILES = 20000
MAX_BUNDLE_BYTES = 4 * 1024 ** 3
_TOKEN_SPLIT_RE = re.compile(fingerprints.PATH_SEPARATORS)
_DAG_CONSTANTS = ("HAMMER_EXEC", "WORK_DIR", "OBJ_DIR", "ENV_CONFIGS", "PROJ_CONFIGS", "DESIGN_NAME",
                  "PROJ_CONFS_BY_TOOLS", "DEFAULT_TOOLS")


class ShareError(RuntimeError):
    """A design cannot be bundled, pulled or rebuilt; the message says why and what to do."""


# --- paths ---------------------------------------------------------------------------------------------

def _forms(path: str) -> Tuple[str, ...]:
    return tuple(dict.fromkeys((os.path.normpath(os.path.abspath(path)), os.path.realpath(path))))


def _relative(path: str, root: str) -> Optional[str]:
    """path relative to root ('' for root itself) if it is inside it in either normpath or realpath form."""
    for p in _forms(path):
        for r in _forms(root):
            if p == r:
                return ""
            if p.startswith(r.rstrip(os.sep) + os.sep):
                return p[len(r.rstrip(os.sep)) + 1:].replace(os.sep, "/")
    return None


def _excluded_roots() -> List[str]:
    """Trees a bundle never copies: the hammer package and the Python environment running it."""
    return [fingerprints.hammer_dir(), sys.prefix]


# --- reading what the owner builds ---------------------------------------------------------------------

def read_dag(dag_file: str) -> Dict[str, Any]:
    """The settings a generated DAG bakes in (HAMMER_EXEC, WORK_DIR, OBJ_DIR, the configs, ...), read
    without importing it."""
    try:
        tree = ast.parse(Path(dag_file).read_text(), filename=dag_file)
    except (OSError, SyntaxError) as e:
        raise ShareError(f"cannot read the DAG {dag_file}: {e}") from e
    found: Dict[str, Any] = {}
    for node in tree.body:
        if isinstance(node, ast.Assign) and len(node.targets) == 1 and isinstance(node.targets[0], ast.Name):
            name = node.targets[0].id
            if name in _DAG_CONSTANTS:
                try:
                    found[name] = ast.literal_eval(node.value)
                except ValueError:
                    pass
    missing = [n for n in ("HAMMER_EXEC", "WORK_DIR", "OBJ_DIR", "ENV_CONFIGS", "PROJ_CONFIGS") if n not in found]
    if missing:
        raise ShareError(f"{dag_file} is not a SledgeHammer DAG (no {', '.join(missing)}); "
                         f"regenerate it with make buildfile.")
    if not isinstance(found.get("PROJ_CONFS_BY_TOOLS"), dict):
        found["PROJ_CONFS_BY_TOOLS"] = {"default": list(found["PROJ_CONFIGS"])}
        found["DEFAULT_TOOLS"] = "default"
    found.setdefault("DESIGN_NAME", os.path.basename(str(found["OBJ_DIR"]).rstrip("/")))
    return found


def _candidate_paths(value: Any, work_dir: str, suffixes: Tuple[str, ...]) -> Iterator[str]:
    """Absolute paths a config value names: absolute tokens, and relative ones (resolved against the
    working directory, as the tools resolve them) that look like a path or a collateral file."""
    if isinstance(value, dict):
        for v in value.values():
            yield from _candidate_paths(v, work_dir, suffixes)
    elif isinstance(value, (list, tuple)):
        for v in value:
            yield from _candidate_paths(v, work_dir, suffixes)
    elif isinstance(value, str):
        for token in _TOKEN_SPLIT_RE.split(value):
            if not token:
                continue
            if os.path.isabs(token):
                yield os.path.normpath(token)
            elif "/" in token or token.lower().endswith(suffixes):
                yield os.path.normpath(os.path.join(work_dir, token))


def _walk(directory: str, prune: Callable[[str], bool]) -> Iterator[str]:
    seen = set()
    for top, dirs, files in os.walk(directory, followlinks=True):
        real = os.path.realpath(top)
        if real in seen:
            dirs[:] = []
            continue
        seen.add(real)
        dirs[:] = sorted(d for d in dirs if not d.startswith(".") and not prune(os.path.join(top, d)))
        for name in sorted(files):
            path = os.path.join(top, name)
            if not fingerprints.skip_name(name) and os.path.isfile(path):
                yield path


# --- building a bundle ---------------------------------------------------------------------------------

class Bundle:
    def __init__(self, manifest: Dict[str, Any], data: Optional[bytes], bundle_sha: str,
                 files: Optional[Dict[str, str]] = None) -> None:
        self.manifest = manifest
        self._data = data
        self._files = files or {}
        self.bundle_sha = bundle_sha

    @property
    def data(self) -> bytes:
        """The packed files, packed on first use so a bundle that turns out unchanged is never packed."""
        if self._data is None:
            self._data = _pack(self._files)
        return self._data


def _sha256(path: str) -> str:
    return fingerprints.sha256_file(path)


def _local_modules(root: str, name: str) -> List[Tuple[str, str]]:
    """The files ``import name`` loads from root, the directory `python <driver>` puts first on sys.path, as
    (path, package) for each module on the way."""
    found: List[Tuple[str, str]] = []
    parts = name.split(".")
    here = root
    for i, part in enumerate(parts):
        init = os.path.join(here, part, "__init__.py")
        if os.path.isfile(init):
            found.append((init, ".".join(parts[:i + 1])))
        elif os.path.isfile(os.path.join(here, part + ".py")):
            found.append((os.path.join(here, part + ".py"), ".".join(parts[:i])))
            break
        elif not os.path.isdir(os.path.join(here, part)):
            break
        here = os.path.join(here, part)
    return found


def _local_module(root: str, name: str) -> Optional[Tuple[Optional[str], str]]:
    """(file, package) of the module ``import name`` loads from root, with no file for a directory without
    __init__.py, or None when root holds no such module."""
    base = os.path.join(root, *name.split("."))
    if os.path.isfile(os.path.join(base, "__init__.py")):
        return os.path.join(base, "__init__.py"), name
    if os.path.isfile(base + ".py"):
        return base + ".py", name.rpartition(".")[0]
    return (None, name) if os.path.isdir(base) else None


_PATH_CALLS: Dict[str, Callable[..., Any]] = {
    "os.path.join": os.path.join, "os.path.dirname": os.path.dirname, "os.path.abspath": os.path.abspath,
    "os.path.realpath": os.path.realpath, "os.path.normpath": os.path.normpath, "pathlib.Path": Path, "str": str}


class _Ref:
    """A module or function a hook module names: a local module with its (file, package), else a dotted name."""

    def __init__(self, dotted: str, local: Optional[Tuple[Optional[str], str]] = None) -> None:
        self.dotted = dotted
        self.local = local


class _PathValues:
    """The values of hook modules' path expressions, worked out from their source without running it."""

    def __init__(self, root: str, carried: Callable[[str], bool]) -> None:
        from hammer.vlsi import code_fingerprints
        self.root = root
        self.carried = carried
        self.trees: Dict[str, Optional[ast.Module]] = {}
        self.envs: Dict[str, Dict[str, Any]] = {}
        self._module = code_fingerprints._HookModule

    def tree(self, real: str) -> Optional[ast.Module]:
        if real not in self.trees:
            self.trees[real] = self._module(real, None).tree
        return self.trees[real]

    def ref(self, dotted: str) -> _Ref:
        local = None if dotted.split(".")[0] in ("os", "pathlib") else _local_module(self.root, dotted)
        if local is not None and local[0] is not None and not self.carried(local[0]):
            local = None
        return _Ref(dotted, local)

    def member(self, base: Any, attr: str) -> Any:
        if isinstance(base, Path):
            return base.parent if attr == "parent" else None
        if not isinstance(base, _Ref):
            return None
        if base.local is None:
            return _Ref(f"{base.dotted}.{attr}")
        sub = self.ref(f"{base.dotted}.{attr}")
        if sub.local is not None:
            return sub
        return self.env(*base.local).get(attr) if base.local[0] else None

    def env(self, path: str, package: str) -> Dict[str, Any]:
        """The module's top-level imports and assignments, with __file__ the path it is loaded from."""
        key = os.path.realpath(path)
        if key in self.envs:
            return self.envs[key]
        env = self.envs[key] = {"__file__": path}
        tree = self.tree(key)
        for st in (tree.body if tree is not None else []):
            if isinstance(st, ast.Import):
                for a in st.names:
                    env[a.asname or a.name.split(".")[0]] = self.ref(a.name if a.asname else a.name.split(".")[0])
            elif isinstance(st, ast.ImportFrom):
                base = package.split(".") if package and st.level else []
                if st.level > 1:
                    base = base[:-(st.level - 1)]
                dotted = ".".join(base + ([st.module] if st.module else []))
                target = self.ref(dotted) if dotted else None
                for a in st.names:
                    if a.name != "*":
                        env[a.asname or a.name] = self.member(target, a.name)
                    elif target is not None and target.local is not None and target.local[0]:
                        env.update((k, v) for k, v in self.env(*target.local).items() if not k.startswith("_"))
            elif isinstance(st, (ast.Assign, ast.AnnAssign)) and st.value is not None:
                try:
                    value = self.value(st.value, env)
                except Exception:
                    value = None
                for t in (st.targets if isinstance(st, ast.Assign) else [st.target]):
                    if isinstance(t, ast.Name):
                        env[t.id] = value
        return env

    def value(self, node: ast.AST, env: Dict[str, Any], partial: bool = False) -> Any:
        """node's str, Path or _Ref value, or None; with partial, an f-string's unknown parts are NUL."""
        if isinstance(node, ast.Constant):
            return node.value if isinstance(node.value, str) else None
        if isinstance(node, ast.Name):
            return env.get(node.id, _Ref("str") if node.id == "str" else None)
        if isinstance(node, ast.Attribute):
            return self.member(self.value(node.value, env), node.attr)
        if isinstance(node, ast.JoinedStr):
            parts = []
            for v in node.values:
                x = None
                if isinstance(v, ast.Constant):
                    x = v.value
                elif isinstance(v, ast.FormattedValue) and v.format_spec is None and v.conversion in (-1, 115):
                    x = self.value(v.value, env)
                if not isinstance(x, (str, Path)):
                    if not partial:
                        return None
                    x = "\0"
                parts.append(str(x))
            return "".join(parts)
        if isinstance(node, ast.BinOp) and isinstance(node.op, (ast.Add, ast.Div)):
            left, right = self.value(node.left, env), self.value(node.right, env)
            if isinstance(node.op, ast.Add):
                return left + right if isinstance(left, str) and isinstance(right, str) else None
            if isinstance(left, (str, Path)) and isinstance(right, (str, Path)) and (
                    isinstance(left, Path) or isinstance(right, Path)):
                return left / right
            return None
        if isinstance(node, ast.Call) and not node.keywords and not any(isinstance(a, ast.Starred) for a in node.args):
            if isinstance(node.func, ast.Attribute) and node.func.attr in ("resolve", "absolute") and not node.args:
                base = self.value(node.func.value, env)
                return getattr(base, node.func.attr)() if isinstance(base, Path) else None
            func = self.value(node.func, env)
            call = _PATH_CALLS.get(func.dotted) if isinstance(func, _Ref) and func.local is None else None
            args = [self.value(a, env) for a in node.args] if call is not None else []
            if call is not None and all(isinstance(a, (str, Path)) for a in args):
                return call(*args)
        return None


def _hook_files(root: str, modules: Iterable[str], carried: Callable[[str], bool]) -> Tuple[List[str], List[str]]:
    """The local modules these modules import, followed from root as `python <driver>` imports them while
    ``carried`` accepts them, and the files all of them name, by literal or by a path expression of their own
    top-level values."""
    from types import SimpleNamespace

    from hammer.vlsi import code_fingerprints
    values = _PathValues(root, carried)
    todo = [(os.path.abspath(p), "") for p in modules]
    seen: Dict[str, None] = {}
    imported: List[str] = []
    named: Dict[str, None] = {}
    while todo:
        path, package = todo.pop(0)
        real = os.path.realpath(path)
        if real in seen:
            continue
        seen[real] = None
        tree = values.tree(real)
        if tree is None:
            continue
        env = values.env(path, package)
        found = list(env.values())
        for x in ast.walk(tree):
            if isinstance(x, (ast.JoinedStr, ast.BinOp, ast.Call)):
                try:
                    found.append(values.value(x, env, partial=True))
                except Exception:
                    pass
            if isinstance(x, (ast.JoinedStr, ast.BinOp)):
                frags = sorted((c for c in ast.walk(x) if isinstance(c, ast.Constant) and isinstance(c.value, str)),
                               key=lambda c: (getattr(c, "lineno", 0), getattr(c, "col_offset", 0)))
                found.append("/".join(c.value.strip("/") for c in frags if c.value.strip("/")))
        strings = list(dict.fromkeys(str(v) for v in found if isinstance(v, (str, Path))))
        strings += [t.split("\0")[0] for s in strings if re.search(r"[\s\0]", s) for t in s.split()]
        for p in dict.fromkeys((path, real)):
            mod = SimpleNamespace(path=p, live=None)
            named.update(dict.fromkeys(sorted(code_fingerprints._literal_paths(mod, [tree], strings))))
        for name in sorted(code_fingerprints._tree_imports(tree, package)):
            for found_path, pkg in _local_modules(root, name):
                if found_path not in imported and carried(found_path):
                    imported.append(found_path)
                    todo.append((found_path, pkg))
    return imported, sorted(named)


def build_bundle(design: str, work_dir: str, obj_dir: str, env_configs: Iterable[str],
                 proj_confs_by_tools: Dict[str, List[str]], default_tools: str, hammer_exec: Optional[str],
                 config: Dict[str, Any], owner: Optional[str] = None) -> Bundle:
    """Pack the files a partner needs to build ``design`` as the owner does; ``config`` is the owner's merged
    config (HammerDriver.database), whose values name the RTL and collateral."""
    work_dir = os.path.normpath(os.path.abspath(work_dir))
    obj_dir = os.path.normpath(os.path.abspath(obj_dir))
    obj_rel = _relative(obj_dir, work_dir)
    excluded = _excluded_roots()
    files: Dict[str, str] = {}  # arcname -> absolute path
    external: Dict[str, None] = {}

    def arcname(path: str) -> Optional[str]:
        """Where a file lives in the bundle, or None when it is not the bundle's to carry."""
        if any(_relative(path, root) is not None for root in excluded):
            return None
        in_obj = _relative(path, obj_dir)
        if in_obj is not None:
            # The build dir holds stage outputs, which the PD cache or a rerun recreates. Only files at
            # its top level (generated inputs such as sram_generator-output.json) belong to the design.
            return f"obj/{in_obj}" if in_obj and "/" not in in_obj else None
        in_work = _relative(path, work_dir)
        if in_work:
            return f"work/{in_work}"
        return None

    def add_file(path: str) -> Optional[str]:
        name = arcname(path)
        if name is not None:
            files[name] = path
        elif not any(_relative(path, root) is not None for root in excluded + [obj_dir, work_dir]):
            external[os.path.normpath(path)] = None
        return name

    def prune(path: str) -> bool:
        return (_relative(path, obj_dir) is not None
                or any(_relative(path, root) is not None for root in excluded))

    def add_path(path: str) -> None:
        if _relative(work_dir, path) is not None:
            return  # the working directory itself or a directory above it: never the whole tree
        if os.path.isfile(path):
            add_file(path)
        elif os.path.isdir(path) and not prune(path):
            if _relative(path, work_dir) is None:
                external[os.path.normpath(path)] = None
                return
            for f in _walk(path, prune):
                add_file(f)
                if len(files) > MAX_BUNDLE_FILES:
                    raise ShareError(f"{path} holds more than {MAX_BUNDLE_FILES} files; a bundle that big "
                                     f"is not a design. Point the config at a narrower directory.")

    def config_arcnames(paths: Iterable[str]) -> List[str]:
        out = []
        for p in paths:
            name = add_file(os.path.normpath(os.path.abspath(p)))
            out.append(name if name is not None else os.path.normpath(os.path.abspath(p)))
        return out

    env_arcs = config_arcnames(env_configs)
    tools_arcs = {tool: config_arcnames(confs) for tool, confs in proj_confs_by_tools.items()}
    driver_arc = None
    rewrite = set(a for a in env_arcs + [a for arcs in tools_arcs.values() for a in arcs] if "/" in a
                  and not os.path.isabs(a))
    if hammer_exec:
        driver_arc = add_file(os.path.normpath(os.path.abspath(hammer_exec)))
        if driver_arc is not None:
            rewrite.add(driver_arc)
            # hooks split out of the driver into modules beside it
            here = os.path.dirname(os.path.abspath(hammer_exec))
            modules = [hammer_exec]
            for name in sorted(os.listdir(here)):
                if name.endswith(".py") and os.path.isfile(os.path.join(here, name)):
                    arc = add_file(os.path.join(here, name))
                    if arc is not None:
                        rewrite.add(arc)
                        modules.append(os.path.join(here, name))
            imported, named = _hook_files(here, modules, lambda p: arcname(p) is not None)
            for path in imported:
                arc = add_file(path)
                if arc is not None:
                    rewrite.add(arc)
            for path in named:
                add_file(path)
    suffixes = fingerprints.collateral_suffixes() + fingerprints.RTL_INCLUDE_SUFFIXES
    for path in dict.fromkeys(_candidate_paths(config, work_dir, suffixes)):
        if os.path.exists(path):
            add_path(path)

    total = sum(os.path.getsize(p) for p in files.values())
    if total > MAX_BUNDLE_BYTES:
        raise ShareError(f"the design's files add up to {total / 1024 ** 3:.1f} GiB, over the "
                         f"{MAX_BUNDLE_BYTES / 1024 ** 3:.0f} GiB a bundle may hold.")
    hashes = {name: _sha256(path) for name, path in sorted(files.items())}
    manifest: Dict[str, Any] = {
        "version": BUNDLE_VERSION,
        "design": design,
        "owner": owner,
        "source": {"work_dir": work_dir, "obj_dir": obj_dir},
        "obj_dir_rel": obj_rel,
        "env_configs": env_arcs,
        "proj_confs_by_tools": tools_arcs,
        "default_tools": default_tools if default_tools in tools_arcs else sorted(tools_arcs)[0],
        "driver": driver_arc,
        "rewrite": sorted(rewrite),
        "files": hashes,
        "external": sorted(external),
        "hammer": hammer_identity(),
    }
    bundle_sha = hashlib.sha256(json.dumps(manifest, sort_keys=True).encode()).hexdigest()
    manifest["created_at"] = datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds")
    return Bundle(manifest, None, bundle_sha, files)


def _pack(files: Dict[str, str]) -> bytes:
    """gzip tar of the files under their arcnames, mtimes kept (fingerprints key files too big to hash by
    whole-second mtime) and owners scrubbed."""
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz", dereference=True) as tar:
        for name in sorted(files):
            info = tar.gettarinfo(files[name], arcname=name)
            info.uid = info.gid = 0
            info.uname = info.gname = ""
            info.mode &= 0o755
            with open(files[name], "rb") as f:
                tar.addfile(info, f)
    return buf.getvalue()


def hammer_identity() -> Dict[str, Optional[str]]:
    """The Hammer a bundle was made with. Its framework fingerprint is part of every cache key, so a
    partner on a different Hammer cannot hit the owner's cache."""
    from hammer.vlsi import code_fingerprints
    commit = None
    try:
        out = subprocess.run(["git", "-C", fingerprints.hammer_dir(), "rev-parse", "HEAD"],
                             capture_output=True, text=True, timeout=10)
        commit = out.stdout.strip() or None if out.returncode == 0 else None
    except (OSError, subprocess.SubprocessError):
        commit = None
    try:
        framework = code_fingerprints.framework_fingerprint()
    except Exception:
        framework = None
    return {"commit": commit, "framework_fingerprint": framework}


@contextlib.contextmanager
def _cwd(path: str) -> Iterator[None]:
    old = os.getcwd()
    os.chdir(path)
    try:
        yield
    finally:
        os.chdir(old)


def bundle_from_dag(dag_file: str, owner: Optional[str] = None) -> Bundle:
    """Bundle the design a generated DAG builds, reading its merged config the way its tasks do."""
    from hammer.vlsi import HammerDriver, HammerDriverOptions
    dag = read_dag(dag_file)
    tools = dag["PROJ_CONFS_BY_TOOLS"]
    default_tools = dag.get("DEFAULT_TOOLS") if dag.get("DEFAULT_TOOLS") in tools else sorted(tools)[0]
    with _cwd(dag["WORK_DIR"]):
        driver = HammerDriver(HammerDriverOptions(
            environment_configs=list(dag["ENV_CONFIGS"]), project_configs=list(tools[default_tools]),
            log_file=os.path.join(dag["OBJ_DIR"], "studio-share.log"), obj_dir=dag["OBJ_DIR"]))
        config = json.loads(driver.database.get_database_json())
        return build_bundle(dag["DESIGN_NAME"], dag["WORK_DIR"], dag["OBJ_DIR"], dag["ENV_CONFIGS"], tools,
                            default_tools, dag["HAMMER_EXEC"], config, owner)


def bundle_from_driver(driver: Any, owner: Optional[str] = None) -> Bundle:
    """Bundle the design a running action builds: the DAG in its build dir if it has one (all its tools
    choices), else the configs this action was given."""
    from hammer.config import HammerJSONEncoder, combine_configs
    dag_file = os.path.join(driver.obj_dir, "hammer_dag.py")
    db = driver.database
    # The tools and runtime layers hold only this stage's tool defaults and settings, so every stage scans what
    # studio share scans.
    config = json.loads(json.dumps(combine_configs([{}] + db.builtins + db.core + db.technology + db.environment
                                                   + db.project), cls=HammerJSONEncoder))
    design = os.environ.get("HAMMER_AIRFLOW_DESIGN") or os.path.basename(str(driver.obj_dir).rstrip("/"))
    if os.path.isfile(dag_file):
        dag = read_dag(dag_file)
        tools = dag["PROJ_CONFS_BY_TOOLS"]
        default_tools = dag.get("DEFAULT_TOOLS") if dag.get("DEFAULT_TOOLS") in tools else sorted(tools)[0]
        return build_bundle(design, dag["WORK_DIR"], driver.obj_dir, dag["ENV_CONFIGS"], tools, default_tools,
                            dag["HAMMER_EXEC"], config, owner)
    return build_bundle(design, os.getcwd(), driver.obj_dir, driver.options.environment_configs,
                        {"default": list(driver.options.project_configs)}, "default", sys.argv[0], config, owner)


def _driver_work_dir(driver: Any) -> str:
    """The working directory bundle_from_driver packs for a running action."""
    dag_file = os.path.join(driver.obj_dir, "hammer_dag.py")
    return read_dag(dag_file)["WORK_DIR"] if os.path.isfile(dag_file) else os.getcwd()


def republish_if_shared(driver: Any) -> Optional[str]:
    """After syn, par, drc or lvs commits: if this design is shared, publish its bundle again when its files
    changed, so partners who pull get what this run built. Only the checkout the stored bundle was published
    from (by real path) republishes it; a bundle that records no source is never republished from a pulled
    copy. Returns a one-line note, or None when nothing was done. Never raises; sharing must not fail a stage
    that succeeded."""
    from hammer.vlsi import pd_cache, pd_store
    try:
        if not pd_cache.is_cache_enabled(driver):
            return None
        design = os.environ.get("HAMMER_AIRFLOW_DESIGN") or os.path.basename(str(driver.obj_dir).rstrip("/"))
        if not pd_store.design_has_shares(design):
            return None
    except Exception:
        return None  # no database, or no sharing tables yet: nothing is shared that could need refreshing
    try:
        # Without a DAG, a par, drc or lvs action's only project config is its stage input, not the design.
        if not os.path.isfile(os.path.join(driver.obj_dir, "hammer_dag.py")) and any(
                _relative(p, driver.obj_dir) is not None
                for p in getattr(getattr(driver, "options", None), "project_configs", None) or []):
            return None
        work_dir = _driver_work_dir(driver)
        stored = pd_store.stored_design_bundle(design) or {}
        source = (stored.get("source") or {}).get("work_dir")
        if source:
            if os.path.realpath(source) != os.path.realpath(work_dir):
                return (f"left the shared {design} bundle as it is, since it was published from {source}, not "
                        f"from {work_dir}; studio publish {design} --obj-dir {driver.obj_dir} shares this "
                        f"checkout instead")
        elif os.path.isfile(os.path.join(os.path.realpath(work_dir), STATE_FILE)):
            return None
        bundle = bundle_from_driver(driver, owner=pd_store.current_db_user())
        if bundle.bundle_sha == stored.get("bundle_sha"):
            return None
        if pd_store.publish_design_bundle(design, bundle.manifest, bundle.data, bundle.bundle_sha):
            return f"published the updated {design} bundle for its partners ({len(bundle.data)} bytes)"
        return None
    except Exception as e:
        return f"could not refresh the shared {driver.obj_dir} bundle ({type(e).__name__}: {e})"


# --- pulling --------------------------------------------------------------------------------------------

def _inside(root: str, real: str) -> bool:
    """Whether the real path ``real`` is the real path ``root`` or lies under it."""
    return os.path.commonpath([root, real]) == root


def _real_entry(path: str) -> str:
    """``path`` with its parent folder resolved, so each file has one name however the folder is reached."""
    return os.path.join(os.path.realpath(os.path.dirname(path)), os.path.basename(path))


def _member_sha256(tar: tarfile.TarFile, member: tarfile.TarInfo) -> str:
    src = tar.extractfile(member)
    return hashlib.sha256(src.read() if src is not None else b"").hexdigest()


def _check_member(member: tarfile.TarInfo) -> str:
    """The member's relative path, or ShareError for anything but a plain file or directory inside
    work/ or obj/."""
    name = member.name
    parts = name.split("/")
    if (os.path.isabs(name) or ".." in parts or not parts or parts[0] not in ("work", "obj")
            or not (member.isfile() or member.isdir())):
        raise ShareError(f"the bundle holds {name!r}, which a design bundle never contains; refusing it.")
    return "/".join(parts[1:])


def _rewrite(text: str, pairs: List[Tuple[str, str]]) -> str:
    new: Dict[str, str] = {}
    for old, replacement in pairs:
        new.setdefault(old, replacement)
    if not new:
        return text
    # one pass, longest prefix first, so a replacement is never rewritten again
    olds = "|".join(map(re.escape, sorted(new, key=len, reverse=True)))
    return re.sub(f"(?:{olds})" + r"(?=$|[/\s\"'{}\[\]();,=<>|:])", lambda m: new[m.group(0)], text)


def unpack(data: bytes, manifest: Dict[str, Any], work_dir: str, obj_dir: str) -> List[str]:
    """Write a bundle into work_dir (and obj_dir for build-dir files), with the owner's absolute paths in
    the configs and driver pointed at these directories. Returns the files written, relative to work_dir
    or as absolute paths for build-dir files."""
    source = manifest.get("source") or {}
    pairs: List[Tuple[str, str]] = []
    if manifest.get("obj_dir_rel") is None and source.get("obj_dir"):
        pairs += [(form, obj_dir) for form in _forms(source["obj_dir"])]
    if source.get("work_dir"):
        pairs += [(form, work_dir) for form in _forms(source["work_dir"])]
    pairs.sort(key=lambda p: -len(p[0]))
    rewrite = set(manifest.get("rewrite") or [])
    expected = manifest.get("files") or {}
    written = []
    real_work = os.path.realpath(work_dir)
    with tarfile.open(fileobj=io.BytesIO(data), mode="r:gz") as tar:
        plan = []
        for member in tar.getmembers():
            rel = _check_member(member)
            root = work_dir if member.name.startswith("work/") else obj_dir
            dest = os.path.join(root, *rel.split("/")) if rel else root
            real = os.path.realpath(dest if member.isdir() else os.path.dirname(dest))
            if not _inside(real_work, real):
                target = real if member.isdir() else os.path.join(real, os.path.basename(dest))
                raise ShareError(f"the bundle's {member.name!r} would be written to {target}, which is outside "
                                 f"{work_dir}; refusing the bundle.")
            if expected and member.isfile() and expected.get(member.name) != _member_sha256(tar, member):
                raise ShareError(f"the bundle's {member.name!r} does not match its manifest, so the bundle is "
                                 f"damaged; ask its owner to publish it again.")
            plan.append((member, rel, dest))
        if expected and {m.name for m, _, _ in plan if m.isfile()} != set(expected):
            raise ShareError("the bundle's files do not match its manifest, so the bundle is damaged; ask its owner "
                             "to publish it again.")
        for member, rel, dest in plan:
            if member.isdir():
                os.makedirs(dest, exist_ok=True)
                continue
            os.makedirs(os.path.dirname(dest), exist_ok=True)
            src = tar.extractfile(member)
            body = src.read() if src is not None else b""
            if member.name in rewrite:
                body = _rewrite(body.decode("utf-8", "surrogateescape"), pairs).encode("utf-8", "surrogateescape")
            tmp = f"{dest}.{os.getpid()}.tmp"
            with open(tmp, "wb") as f:
                f.write(body)
            os.chmod(tmp, (member.mode & 0o755) | 0o600)
            os.replace(tmp, dest)
            if member.name not in rewrite:
                os.utime(dest, (member.mtime, member.mtime))
            written.append(rel if member.name.startswith("work/") else dest)
    return written


def _local_path(arc: str, work_dir: str, obj_dir: str) -> str:
    if arc.startswith("work/"):
        return os.path.join(work_dir, *arc[len("work/"):].split("/"))
    if arc.startswith("obj/"):
        return os.path.join(obj_dir, *arc[len("obj/"):].split("/"))
    return arc  # an absolute path outside the owner's checkout, used as is


def pull(owner: str, design: str, into: str, env_configs: Optional[List[str]] = None, force: bool = False,
         fetch: Optional[Callable[[str, str], Optional[Dict[str, Any]]]] = None,
         build: bool = True) -> Dict[str, Any]:
    """Fetch ``owner``'s bundle of ``design`` into ``into`` and generate the caller's own DAG for it.

    ``into`` becomes the build's working directory: the DAG runs from it, so the cache keys match the
    owner's. Pulling again updates it to the owner's latest bundle and removes files the owner dropped.
    """
    if fetch is None:
        from hammer.vlsi import pd_store
        fetch = pd_store.fetch_design_bundle
    rec = fetch(owner, design)
    if rec is None:
        raise ShareError(f"no bundle of {design!r} from {owner!r} is visible to you. Either it was never "
                         f"published, or {owner} has not shared it with you (they run: studio share {design} "
                         f"{getpass.getuser()}).")
    manifest = rec["manifest"]
    if manifest.get("version") != BUNDLE_VERSION:
        raise ShareError(f"the bundle is format {manifest.get('version')}, this Hammer reads {BUNDLE_VERSION}; "
                         f"update Hammer.")
    size = rec.get("size_bytes")
    if size is not None and len(rec["data"]) != size:
        raise ShareError(f"{owner}'s bundle of {design} came back with {len(rec['data'])} of its {size} bytes, so "
                         f"it is damaged; ask {owner} to publish it again.")
    work_dir = os.path.realpath(into)
    state_path = os.path.join(work_dir, STATE_FILE)
    previous: Dict[str, Any] = {}
    if os.path.isfile(state_path):
        previous = json.loads(Path(state_path).read_text())
        if (previous.get("owner"), previous.get("design")) != (owner, design):
            raise ShareError(f"{work_dir} holds {previous.get('owner')}'s {previous.get('design')}; pull "
                             f"{owner}'s {design} into another directory.")
    elif os.path.isdir(work_dir) and os.listdir(work_dir) and not force:
        raise ShareError(f"{work_dir} is not empty and was not made by a pull; pull into a new directory, or "
                         f"pass --force to write into it anyway.")
    obj_rel = manifest.get("obj_dir_rel")
    if obj_rel is not None and (not isinstance(obj_rel, str) or os.path.isabs(obj_rel)
                                or ".." in obj_rel.split("/")):
        raise ShareError(f"the bundle puts its build dir at {obj_rel!r}, which is outside {work_dir}; refusing the "
                         f"bundle.")
    obj_dir = os.path.normpath(os.path.join(work_dir, *(obj_rel or f"build/{design}").split("/")))
    root, real_obj = os.path.realpath(work_dir), os.path.realpath(obj_dir)
    if not _inside(root, real_obj):
        raise ShareError(f"the bundle's build dir {obj_dir} leads to {real_obj}, which is outside {work_dir}; "
                         f"refusing the bundle.")
    try:
        written = unpack(rec["data"], manifest, work_dir, obj_dir)
    except (tarfile.TarError, EOFError, zlib.error, gzip.BadGzipFile) as e:
        raise ShareError(f"{owner}'s bundle of {design} is damaged ({type(e).__name__}: {e}); ask {owner} to "
                         f"publish it again.") from e
    os.makedirs(obj_dir, exist_ok=True)
    kept = {_real_entry(os.path.normpath(os.path.join(work_dir, w))) for w in written}
    for old in previous.get("files", []):
        if not isinstance(old, str):
            continue
        path = os.path.normpath(os.path.join(work_dir, old))
        real = _real_entry(path)
        if path != work_dir and real not in kept and _inside(root, os.path.dirname(real)):
            with contextlib.suppress(FileNotFoundError):
                os.remove(path)
    missing = [p for p in manifest.get("external", []) if not os.path.exists(p)]
    state = {"owner": owner, "design": design, "bundle_sha": rec["bundle_sha"],
             "bundle_updated_at": rec.get("updated_at"), "files": sorted(written), "obj_dir": obj_dir}
    Path(state_path).write_text(json.dumps(state, indent=2) + "\n")
    summary: Dict[str, Any] = {"work_dir": work_dir, "obj_dir": obj_dir, "files": len(written),
                               "missing_external": missing, "hammer_mismatch": _hammer_mismatch(manifest),
                               "dag_id": dag_id_for(design, owner), "dag_file": None}
    if build:
        summary["dag_file"] = generate_dag(manifest, work_dir, obj_dir, owner, env_configs)
    return summary


def _hammer_mismatch(manifest: Dict[str, Any]) -> Optional[str]:
    theirs = (manifest.get("hammer") or {}).get("framework_fingerprint")
    mine = hammer_identity()
    if theirs and mine["framework_fingerprint"] and theirs != mine["framework_fingerprint"]:
        commit = (manifest.get("hammer") or {}).get("commit")
        return (f"your Hammer ({mine['commit'] or 'unknown commit'}) differs from the one the bundle was made "
                f"with ({commit or 'unknown commit'}). Every cache key includes Hammer's own code, so the "
                f"owner's results will not be reused until you run the same Hammer.")
    return None


def dag_id_for(design: str, owner: str, user: Optional[str] = None) -> str:
    """The pulled design's DAG id. It names the owner, so it never replaces the caller's own DAG of a design
    with the same name, and `sledgehammer run` finds it from the pull directory like any other."""
    user = user or getpass.getuser()
    return re.sub(r"[^A-Za-z0-9_.-]", "_", f"sledgehammer_{design}_from_{owner}_{user}")


def generate_dag(manifest: Dict[str, Any], work_dir: str, obj_dir: str, owner: str,
                 env_configs: Optional[List[str]] = None) -> str:
    """Build the caller's own DAG for a pulled design, the way make buildfile does: the owner's driver (or
    this Hammer's hammer-vlsi) runs `build` from the pull directory, so the DAG runs from there too."""
    design = manifest["design"]
    tools = manifest["proj_confs_by_tools"]
    proj = [_local_path(a, work_dir, obj_dir) for a in tools[manifest["default_tools"]]]
    envs = env_configs or [_local_path(a, work_dir, obj_dir) for a in manifest["env_configs"]]
    driver = manifest.get("driver")
    exe = _local_path(driver, work_dir, obj_dir) if driver else os.path.join(os.path.dirname(sys.executable),
                                                                             "hammer-vlsi")
    build_config = os.path.join(work_dir, BUILD_CONFIG)
    Path(build_config).write_text(
        "# Written by studio pull: build this design as a SledgeHammer DAG of your own.\n"
        "vlsi.core.build_system: sledgehammer\n"
        f"vlsi.core.airflow_dag_id: {dag_id_for(design, owner)}\n")
    cmd = [sys.executable, exe]
    for e in envs:
        cmd += ["-e", e]
    for p in proj + [build_config]:
        cmd += ["-p", p]
    cmd += ["--obj_dir", obj_dir, "-o", os.path.join(obj_dir, "studio-pull-build.json"),
            "--log", os.path.join(obj_dir, "studio-pull-build.log"), "build"]
    env = dict(os.environ)
    env["PATH"] = os.path.dirname(sys.executable) + os.pathsep + env.get("PATH", "")
    res = subprocess.run(cmd, cwd=work_dir, env=env, stdin=subprocess.DEVNULL, capture_output=True, text=True)
    if res.returncode != 0:
        raise ShareError(f"the files are in {work_dir}, but generating your DAG failed:\n"
                         f"  {' '.join(cmd)}\n{(res.stderr or res.stdout).strip()[-2000:]}")
    return os.path.join(obj_dir, "hammer_dag.py")
