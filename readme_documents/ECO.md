# Functional ECO after place and route

A bug found after `par` normally means rerunning synthesis and place and route
from scratch. The `eco` stage avoids that. It takes the placed-and-routed
netlist, compares it with a netlist synthesized from the corrected RTL, and
has Cadence Conformal generate a patch that makes the implemented design match
the fix. It then applies the patch, proves the patched netlist equivalent to
the corrected one, and writes the patched netlist plus an Innovus script with
the same edits.

```text
par ─┬→ par_to_sim / par_to_power / par_to_formal / par_to_timing / par_to_drc / par_to_lvs
     └→ par_to_eco → eco
```

The stage closes the logical ECO loop only. Placing and routing the patch
(incremental `par`) and re-running signoff on the result are not part of it
yet; see [What is not covered yet](#what-is-not-covered-yet).

## Running it

The stage needs one input `par` does not produce: the corrected design.
Synthesize the fixed RTL in a **separate** build directory (the build you are
patching keeps its original RTL), with the same technology, tools and
constraints, for example:

```bash
cd e2e
make syn design=gcd OBJ_DIR=$PWD/build-sky130-cm/gcd-fixed extra=configs-design/gcd/fixed-rtl.yml
```

where `fixed-rtl.yml` points `synthesis.inputs.input_files` at the corrected
sources. Then name that netlist in the design's `eco.yml`:

```yaml
# e2e/configs-design/gcd/eco.yml
formal.inputs.eco_reference_files: ["build-sky130-cm/gcd-fixed/syn-rundir/gcd.mapped.v"]
```

and run the stage:

| Flow | Command |
|---|---|
| Make (`e2e/`) | `make eco`; the Makefile adds `configs-design/$(design)/eco.yml` for any goal with `eco` in it |
| DAG | tick **Functional ECO** on the trigger form, or `sledgehammer run eco` |
| Rerun just the ECO | `make redo-eco`; in the DAG `eco` reruns whenever it is asked for, like the other formal stages |

`studio make-dag --design <design>` puts every `configs-design/<design>/*.yml`
in the DAG, so rerun it after adding `eco.yml`.

Asking for `eco` pulls in `syn` and `par` like any later stage, and both skip.
`formal.inputs.eco_reference_files` lives under `formal.*`, which the
dependency check and the fingerprints scope to the formal stages, so adding it
after `par` changes nothing `syn` or `par` depend on. A key outside the stage
namespaces would count as global and rerun every stage.

Conformal settings come from the formal tool's keys:

- `formal.conformal.license` picks the binary mode: `GXL` (default) runs
  `lec -ECOGXL`, `XL` runs `lec -ECO`. On the BWRC license servers
  `Conformal_ECO_GXL` is available and `Conformal_ECO` is not, so keep `GXL`.
- `formal.conformal.version` picks the install. The default `212` segfaults on
  the RHEL 9 BWRC machines; `261` works: `formal.conformal.version: "261"`.
- `formal.conformal.eco_method` picks how the patch is found and where it goes:
  `hierarchical` (default), `flat_localized` or `flat_global`. See
  [Choosing the ECO method](#choosing-the-eco-method).

## What the stage reads and writes

`par_to_eco` (`HammerDriver.par_output_to_eco_input`) turns `par`'s output
into a formal run with `formal.inputs.check: eco`:

| ECO input | Comes from | Conformal side |
|---|---|---|
| `formal.inputs.input_files` | `par.outputs.output_sim_netlist` (`<top>.sim.v`) | golden; the design that gets patched |
| `formal.inputs.reference_files` | `formal.inputs.eco_reference_files` | revised; what the patch must match |

The implemented design is golden because Conformal's ECO commands patch the
golden side: `ANALYZE ECO` "analyzes the ECO change in the Revised root module
when compared to the Golden root module" (Conformal Reference, LEC ECO
commands). This is the reverse of the plain `lec` check, where the reference is
golden. In the reports, `(G)` is the implementation and `(R)` the reference.
| `formal.inputs.top_module` | `par.inputs.top_module` | root module of both |
| Liberty | every library of the setup MMMC corner, from the technology | `read_library -liberty` |

The Liberty is one corner, so the patch maps onto one characterization of each
cell, and every library at that corner, so pads and macros in the netlist have
models (Conformal stops on a cell it has none for). On sky130 that corner lists
several supply variants of each IO pad. Conformal keeps the first copy (warning
IGN3.2), and the two verification reports gain a "Design ambiguity: Duplicate
module definition" line. Neither changes the patch or the verdicts.

The `eco` stage is the `formal` action run in its own rundir
(`--formal_rundir eco-rundir`), the same way `formal_par` runs in
`formal-par-rundir`. Everything lands in `eco-rundir/`:

| File | What it is |
|---|---|
| `eco.tcl`, `eco.log` | the generated dofile and Conformal's log |
| `01_pre_eco_verification.rpt`, `02_pre_eco_nonequivalent.rpt` | compare before the patch (expected to fail). `hierarchical`: the module-by-module result and the non-equivalent modules |
| `03_eco_check.rpt`, `04_eco_hierarchy_noneq.rpt` | `flat_localized` only: ECO setup checks and the modules that need patching |
| `patch.v`, `ecopin.do`, `hier.eco.do` | the patch, the ECO pins it needs, the hierarchical compare dofile. `hierarchical` also keeps one `patch_<module>.v` per patched module; `patch.v` collects them |
| `golden_flat.v` | `flat_global` only: the implemented netlist flattened, which the patch is applied to |
| `05_post_eco_verification.rpt`, `06_post_eco_compare_data.rpt` | the written `<top>.eco.v`, read back and compared flat against the reference (must pass) |
| `<top>.eco.v`, `07_ECOprelogics.rpt` | the patched netlist and its report |
| `eco.invs` | the patch as Innovus netlist-edit commands |

The stage fails unless `05_post_eco_verification.rpt` reports `PASS`.
Conformal's exit status cannot decide this: it flags the non-equivalent points
of the first compare, which an ECO always has. The final compare reads the
written netlist back rather than comparing in the same session: after a
hierarchical compare, an in-session compare still sees the submodules as black
boxes and checks only the top level (on Test2: 214 points, no flip-flops,
against 2532). When the designs already match
before the patch, the stage passes with a warning that there was nothing to
fix.

`eco-rundir/formal-output.json` exports the artifacts for later stages:
`formal.outputs.eco_netlist`, `formal.outputs.eco_patch` and
`formal.outputs.eco_innovus_script`.

The dofile has the two steps of the plugin's `lec` check, with a Conformal
checkpoint after each: `setup_eco_designs` reads the designs, and
`patch_eco_designs` does the rest of the original dofile. Each checkpoint
costs about 27 s and 195 MB even on gcd, about as much as the ECO itself, so
the ECO is not split further. From the command line, `--to_step
setup_eco_designs` stops after reading (in `e2e/`, `make redo-eco args="..."`)
and `--from_step patch_eco_designs` restarts from that checkpoint. The DAG
trigger form's step fields apply to `syn` and `par` only. A run stopped early
is judged only on the steps it ran.

## Choosing the ECO method

`formal.conformal.eco_method` (default `hierarchical`) picks how Conformal
finds the logic to change and where it puts the patch. All three end with the
same check: the written `<top>.eco.v` is read back and compared flat against
the reference.

| Method | Finds the mismatch by | Patch goes | Conformal steps |
|---|---|---|---|
| `hierarchical` | comparing module by module (`write_hier_compare_dofile`, `run_hier_compare`) | into each non-equivalent module, generated from that module's own compare | per module: `set_root_module`, `compare`, `analyze_eco patch_<module>.v`; then `apply_patch <module> <module>_eco -keephierarchy` |
| `flat_localized` | one flat compare (`set_eco_option -flat`, `analyze_hier_compare -eco_aware`) | into each module the failing logic sits in | `compare_eco_hierarchy`, `analyze_eco -hierarchical`, `apply_patch -auto -keephierarchy`. Cadence's "flattened ECO flow" |
| `flat_global` | one flat compare of the flattened netlists | one patch at the top; the patched netlist comes out flat | `flatten`, `write_design golden_flat.v`, read back; `analyze_eco`, `apply_patch -auto` |

`flat_global` reads a flattened copy of the implementation (`golden_flat.v`)
because Conformal applies a patch only to the netlist structure it read
(`ECO1.12` otherwise), and flattens the reference too, or `analyze_eco` finds
no implementation nets for its gates. On a hierarchical netlist left as it is,
a top-level patch tries to re-implement most of the design (Conformal warns
"Please use hierarchical method").

Measured on `gatelevelgurus_Test2` (bugs in `Branch_comp` and `Imm_gen`), all
three through the plugin, Conformal 26.1:

| | `hierarchical` | `flat_localized` | `flat_global` |
|---|---:|---:|---:|
| Stage time | 241 s | 166 s | 322 s |
| Patch | 1 library cell, 18 primitives, in 2 modules | 0 cells, 17 primitives, in 2 modules | 49 library cells, 510 primitives, at the top |
| Innovus edits | 5 pins detached, 23 connected | 3 pins detached, 16 connected | 58 instances deleted, 319 pins detached, 259 connected |
| Patched netlist | hierarchy kept | hierarchy kept | flat (`riscv_top` + `riscv_top_eco`) |
| Final check | PASS, 2532 points | PASS, 2532 points | PASS, 2530 points |

A flat patched netlist no longer matches the module structure of `par`'s
Innovus database, which matters once the patch is placed and routed
incrementally (not covered yet).

## Where this came from

The flow is the EECS 144 ECO project
([anshthakkarr/eecs144-proj](https://github.com/anshthakkarr/eecs144-proj)),
`eco_patch_generation/conformal_eco.do`, which ran Conformal by hand on
netlists Hammer had produced. What changed:

| EECS 144 project | SledgeHammer |
|---|---|
| `lec -dofile conformal_eco.do`, run by hand | the `eco` DAG stage / `make eco` |
| `cadencerc` puts `CONFRML201/bin` on `PATH` | `formal.conformal.version` and `cadence.*` from the env config |
| `./gcd_coprocessor.sim.v` | `par.outputs.output_sim_netlist`, via `par_to_eco` |
| `./gcd_coprocessor_correct.mapped.v` | `formal.inputs.eco_reference_files` |
| `./sky130_ss_1.62_125_nldm.lib.gz` | the technology's setup-corner Liberty; with `technology.sky130.stdcell_library: sky130_scl` it includes that same file |
| `set root module gcd_coprocessor` | `par.inputs.top_module` |
| native dofile syntax | Conformal Tcl mode, which the plugin always uses: `report verification > f` becomes `redirect f {report_verification}`, `add compare points` becomes `add_compared_points` |
| `conformal_eco_out/` | `eco-rundir/` (report names unchanged) |
| `gcd_coprocessor.pre.G3.v` | `<top>.eco.v` |
| `clean.sh` | `make redo-eco` / `--force` regenerate the rundir |
| reading `05_post_eco_verification.rpt` by eye | the stage fails unless it says `PASS` |

Running the project's own netlists through the stage (sky130 with
`stdcell_library: sky130_scl`, Conformal 26.1) gives the same `patch.v` (apart
from the tool-version comment), the same patched netlist and the same verdicts
(`FAIL:NONEQ`, then `PASS`) as the project's committed outputs. Against its
original dofile rerun on the same Conformal version, the Innovus script and
the compare-data, ECO-check and hierarchy reports are identical. The two
verification reports differ only by the duplicate-module line above, and
`07_ECOprelogics.rpt` only by printing the netlist's absolute path.

On the e2e `gcd`, a change that zeroes the result bus until B reaches zero
(`result_bits_data = B_zero ? A_reg : 0`) patched in as 16
`sky130_fd_sc_hd__and2_0` cells, and a separate Conformal compare of the
patched netlist against the changed RTL passes.

## What is not covered yet

- **Incremental place and route.** `eco.invs` (or `<top>.eco.v`) has to be
  applied to the routed database: restore `par`'s database, source the
  script, then `ecoPlace` / `ecoRoute`. An `eco_to_par` bridge and a
  `par_eco` stage would consume `formal.outputs.eco_innovus_script`. The
  project's archived `general_eco` flow has an Innovus script for this
  (`baseline_pnr_inv_R2_incremental.tcl`).
- **Signoff on the patched layout.** Timing, DRC, LVS and a final RTL-vs-layout
  equivalence check would hang off that `par_eco` stage the way they hang off
  `par` today.
- **Physical-aware patch optimization.** Conformal's `optimize_patch` (GXL),
  which maps the patch with the SDC, LEF and `par`'s DEF, as the archived flow
  did. The flow here maps the patch onto the setup-corner Liberty only.
- **Hierarchical designs.** The DAG gets an `eco` node per module, but the flow
  has only been run on flat designs.
