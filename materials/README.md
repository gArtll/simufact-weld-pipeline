# Materials

No material cards in this repository: JMatPro exports (`.xmt`) are licence-bound. `.gitignore`: `*.xmt`.

## Expected files

| File | Use | Case field |
|---|---|---|
| `materials/Q420D_SW.xmt` | base material (web / tube, plate) | `materials.base_xmt` |
| `materials/E551T1Ni1C_SW.xmt` | filler wire (torch) | `materials.filler_xmt` |

Sources: own JMatPro export, Simufact Welding material library export, or any `sfMaterialData` file. Other names: case field paths.

## Format: Simufact `.xmt`

XML, UTF-8, root `<sfMaterialData>`. preflight PF2: parse + root element.

### Header fields

| Element | Content |
|---|---|
| `import_date`, `file_version` | import time stamp, format version |
| `class` | `Welding` |
| `group`, `condition` | material group, condition |
| `gb_norm`, `name`, `number` | grade, display name, number |
| `source`, `author`, `comment` | data origin, author, free text |
| `min_temperature` / `max_temperature` | validity range (°C) |
| `min_effective_plastic_strain` / `max_effective_plastic_strain` | strain range |
| `min_strain_rate` / `max_strain_rate` | strain rate range (1/s) |

### Property fields

Scalar value element + `*_table` / `*_table_peak_temperature_pairs` + `*_user_selection_datatype` (scalar or table).

| Property | Elements |
|---|---|
| yield strength | `yield_strength`, `yield_strength_user_selection_datatype` |
| Young's modulus | `youngs_modulus`, `youngs_modulus_table`, `youngs_modulus_table_peak_temperature_pairs` |
| Poisson's ratio | `transverse_contraction`, `transverse_contraction_table` |
| thermal expansion | `thermal_expansion`, `thermal_expansion_table`, `thermal_expansion_table_peak_temperature_pairs` |
| density | `density`, `density_table`, `density_table_peak_temperature_pairs` |
| thermal conductivity | `thermal_conductivity`, `thermal_conductivity_table`, `thermal_conductivity_table_peak_temperature_pairs` |
| specific heat capacity | `specific_heat_capacity`, `specific_heat_capacity_table`, `specific_heat_capacity_table_peak_temperature_pairs` |
| melting | `melting_point`, `solidus_temperature`, `latent_heat` |
| dissipation | `dissipation_factor`, `dissipation_factor_user_selection_datatype` |
| flow curves | `flow_curves_used_approach`, `flow_curves_table_approach` |
| phase transformation | `diffusivity`, `phase_transformation`, `microstructure_used_model` |
| chemistry, other | `chemical_composition`, `misc` |

### Units and tables

Unit attributes on value elements: `dimension`, `unit`, `unit_symbol` (e.g. `dimension="13" unit="3" unit_symbol="MPa"`).

```xml
<youngs_modulus_table_peak_temperature_pairs>
  <table_peak_temperature_pair>
    <peak_temperature dimension="8" unit="1" unit_symbol="°C">20.0</peak_temperature>
    <table id="0">
      <sfDataTable name="..." type="0" columns="2" rows="149" version="12.1">
        <columndata type="0" datatype="1" fffunit="524289" unit_symbol="°C" usage="1"/>
        <columndata type="0" datatype="1" fffunit="851971" unit_symbol="MPa" usage="2"/>
        <row nb="1">25;209462.9778</row>
      </sfDataTable>
    </table>
  </table_peak_temperature_pair>
</youngs_modulus_table_peak_temperature_pairs>
```

Table row: `<row nb="k">temperature;value</row>`, `;` separator, column units from `columndata`.

## Converter

`jmt2xmt.py`: JMatPro `.jmt` → Simufact `.xmt`.

| Item | Content |
|---|---|
| template | verified `.xmt`, not in the repository; any material exported from the Simufact material library |
| usage | `python materials/jmt2xmt.py <in.jmt> <out.xmt> --template <template.xmt> --name ... --gb ... --ys ... --liquidus ... --solidus ... --latent ... [--selftest]` |
| replaced | all data tables (flow curves transposed to strain-rate groups; property tables), top scalars, metadata |
| Poisson's ratio cap | `--nu-max` 0.48 (< 0.49) |
| selftest | template grade `.jmt` → maximum relative deviation 0 |
