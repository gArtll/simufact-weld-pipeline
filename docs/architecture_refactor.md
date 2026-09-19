# Architecture refactor: from two cases to capabilities

Status: plan and audit, 2026-09-19; phases 1-3 implemented. Phases are implemented in order; each phase ends with tests green.

## 1. What went wrong (a new model, reported from another machine)

A new model, unrelated to the repository's cases, was prepared by an agent on another computer. The agent took,
without being asked and without saying so, the clamping, supports, constraints, heat source, material, filler,
weld speed, time step, cooling time and solver parallelism of the two cases in this repository, and only rebuilt
them after the user objected. It also:

* never produced the status page, because the page was an optional `streamlit run` the agent had to remember;
* hit a contact template (`build/rulesets/fillet.yaml`, user-defined glue on peak temperature) that leaves the web
  free in the first welding increment -- the solver failed twice (3015, 3020) before the template was switched to the
  process default, which is also what Simufact's own `WebOnPlateFromScratch.py` uses;
* rotated its STEP into this repository's frame because `prep` assumes the web thickness is along x;
* found that `mesh_transition/plate.py` accepts an "extrusion" whose two end profiles differ;
* found no runtime estimate anywhere, and a first set-up that would have taken 11 days.

These are symptoms of one design fault: **the repository's decisions are keyed on two case names**, and every value a
case carries is available to be copied into the next one.

## 2. Audit

### 2.1 Where case identity decides behaviour

| Place | What it does |
|---|---|
| `weldsim.py` `FAMILIES = {"fillet_web_on_plate", "fillet_tube_on_plate"}` | any other `geometry_family` is refused before any step |
| `weldsim.py step_hm` | automesh only for `fillet_web_on_plate` |
| `prep/weld_prep.py --section-frame {web_plane, tube_on_plate}` | bead section frame chosen by case family; `web_plane` assumes thickness along x |
| `build/rulesets/{fillet,fillet_tube}.yaml` | contact behaviour per family, one of them user-defined and fragile |
| `cases/example_synthetic.json` | a "synthetic" case that pointed at another case's heat source file: borrowing was the pattern in the repository's own fixture (now its own synthetic XML) |
| `library/参数库_v1.csv`, `weldsim suggest` | ranks old cases by similarity and emits a draft pre-filled from them |
| `ui/app.py` | optional Streamlit page; nothing runs it; nothing depends on it |

### 2.2 What a case carries that must not travel

Tooling (clamping, bearing, fixing, fixed nodes), materials and filler, current, voltage, efficiency, weld speed,
heat source geometry, weld leg, bead section spacing, contact behaviour, welding time step, cooling time and end time,
temperatures and heat transfer, gravity direction, solver domains and threads. Each of these depends on the part,
the process, or the machine.

### 2.3 What may travel

Algorithm defaults (mesh transition templates, row counts, growth limits), quality thresholds, numerical rules
(R1-R24, S1-S10) and file-format rules. These describe the tool, not the part.

### 2.4 Official Simufact Welding 2024.2 material on this machine

`<install>/examples`, `<install>/doc/InfoSheets`, `<install>/doc/Scripting`:

| Official example | Joint / process | Method it shows |
|---|---|---|
| `arc_welding/Singlefillet` | T joint, one-sided single fillet, steel plates | fixings on the web released at 20 s; clamps + bearings on the plate; heat source calibrated in a separate "Calibration" process |
| `arc_welding/WebOnPlate` (+ `scripting/WebOnPlate/WebOnPlateFromScratch.py`) | T joint, two-sided fillets, 2 robots, 4 seams, aluminium | complete scripted build: fixings end 60 s, clamps with force end 30 s, bearing; **no user-defined contact table** |
| `arc_welding/3Robots` | two webs on a plate, 12 fillets, 3 robots | fixings released in stages; fixed nodes to hold the assembly after release |
| `arc_welding/TubeT-Joint` | tube-to-tube T joint, fish-mouth fillets, aluminium | fillet picked from the abutting edge; clamping presses the tubes together |
| `Tutorial/ArcWelding` | tube on plate, circumferential fillet | one bearing, two clampings |
| `arc_welding/Multilayer` | plate with groove, butt, two filler passes (multi-pass) | bearings + one fixing held to the end |
| `laser_beam_welding/Laserwelding`, `Tutorial/LaserBeamWelding`, `Tutorial/ElectronBeamWelding` | two sheets, butt / edge seam, tack welds | side and top clamps released in stages |
| `brazing/Flanged-seam` | flanged seam, straight + curved sheet | filler-only heat input |
| `laser_beam_welding/GearShaft` | circumferential joint gear-shaft | initial gap closed by contact |
| `Tutorial/ThermalCycle` | two I-beams, two seams | local joints to stabilise |
| InfoSheets | `BoundaryConditionsClamping/Bearing/Fixing`, `ContactTable`, `DDMSharedMemoryParallelisation`, `AdvancedSettingsStepSize`, `Exitcodes`, ... | reference for the method, not the numbers |

The examples are references for **method** -- how a boundary condition type is used, when tooling is released, which
contact behaviour is left to the process default, how a multi-pass groove is set up. Their dimensions, tooling
positions and heat source numbers belong to their parts and are never copied.

## 3. Target architecture

```
new (bootstrap from a blank schema)
 -> inspect      STEP -> bodies, faces, joint features            capability/classify.py
 -> plan         features -> capability matrix -> backends/gaps    capability/planner.py, capability/matrix.yaml
 -> provenance   every part/process/machine parameter has a source common/provenance.py
 -> review       dashboard generated from manifests                ui/report.py (static HTML, fixed stage)
 -> hm           mesh backend chosen by the plan
 -> mesh gate    quality acceptance of the mesh backend
 -> preflight    rules + provenance + capability + runtime estimate
 -> prep -> build -> gate -> run -> post -> report
```

Every stage writes `<out>/<stage>/manifest.json` and updates `<out>/lifecycle.json`. The dashboard reads only those
files, so any case -- regression or new -- gets the same page, and it is regenerated after every stage.

### 3.1 Provenance

`case.json` carries `provenance: {<parameter>: {source, ref, note, approval}}`. Sources:

| source | meaning |
|---|---|
| `cad_measured` | measured on the CAD of this part |
| `simufact_official_example` | method taken from an official example or InfoSheet (never its numbers for another part) |
| `user_confirmed` | the user confirmed this value for this part |
| `process_document` | welding procedure specification, drawing or process sheet of this part |
| `derived` | computed by the tool from other values of this case (formula recorded) |
| `estimated` | estimate; allowed with a WARN, listed on the dashboard |
| `borrowed_case` | taken from another case; BLOCK unless the user approved it explicitly, and then still listed |
| `machine_probe` / `benchmark_measured` | hardware facts / smoke-run timing on the machine that will run the job |

Preflight PV rules: missing provenance for a critical parameter = BLOCK; `borrowed_case` without approval = BLOCK;
an input file whose content is byte-identical to an input of a registered regression case = BLOCK unless declared
`borrowed_case` and approved (detects silent copying regardless of the label); `estimated` = WARN.

### 3.2 Capability matrix and planner

Dimensions: parts count; part kinds (plate, tube, profile); joint (butt, fillet/T, lap, corner, edge, tube-on-plate,
tube-tube); sides (single, double); passes (single, multi); seam path (straight, curved, closed); seam count; surface
(planar, oblique, curved); groove (none, V, ...); filler needed; sequence; tooling kind. The planner returns
`detected_joint, required_mesh_backend, bead_strategy, contact_strategy, tooling_inputs_required,
unsupported_features, official_references`. Unsupported combinations are reported as capability gaps; the pipeline
stops instead of falling back to a similar case.

### 3.3 Machine

`solver.domains/threads` come from `machine_probe` (physical/logical cores, memory, licence tokens) and a short
benchmark, never from a repository default.

## 4. Phases

| Phase | Content | Affected modules |
|---|---|---|
| 1 | provenance model + PV rules; `weldsim new` bootstrap from blank schema; machine probe; lifecycle state; dashboard as fixed stage; run gate; regression fixtures marked `role: regression` | `common/provenance.py`, `common/machine.py`, `common/lifecycle.py`, `common/case_template.json`, `common/preflight.py`, `common/rules.yaml`, `ui/report.py`, `weldsim.py`, `cases/*.json` |
| 2 | STEP inspection, joint classification, capability matrix, planner, `official_reference` layer; `weldsim inspect/plan`; FAMILIES gate replaced by the plan | `capability/`, `official/references.yaml`, `weldsim.py` |
| 3 (done) | local joint frame from `inspect`, used by hm (in-plane axis order follows the root) and prep; hm dispatched by the plan (web face, root, plate profile, plate backend); `mapped_plate` for variable-section plates (`unverified` until built and smoke-run); contact strategy per capability with basis, CT1; detected vs supported with evidence; `mesh_gate` recorded | `capability/`, `common/contact.py`, `prep/`, `hm/`, `mesh_transition/`, `weldsim.py`, `ui/report.py` |
| 4 | solver resources measured, not assumed: machine probe + short smoke benchmark over domain x thread layouts (wall time, CPU/wall, memory peak, licence tokens) -> `benchmark_measured` recommendation; runtime estimate; R21 time step vs bead section spacing; clamping fixation start/end pass-through; a 2 s web-on-plate smoke case (which also verifies `mapped_plate` build + run) | `common/`, `build/`, `run/` |
| 5 | new capabilities one at a time: butt joint, lap joint, multi-pass groove, curved seams, multiple parts | `capability/`, backends |

Internal models are regression material only. They never appear in a branch of the main logic, and their data never
enter the public repository.
