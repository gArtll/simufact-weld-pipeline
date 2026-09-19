# -*- coding: utf-8 -*-
"""Synthetic process-parameter and heat-source XML for the generated example cases.

These are NOT Simufact exports and NOT taken from any project: they carry only the elements this repository's checks
read (preflight S7 / PF3 / R12 / R16 / PF5, build hard rule 7, `set_end_time`), with made-up values, in the element
layout Simufact uses. They exercise the pipeline without Simufact; they are not meant to be imported into it."""
import io

HEADER = ('<?xml version="1.0" encoding="UTF-8"?>\n'
          "<!-- SYNTHETIC test fixture written by cases/synthetic_xml.py: not a Simufact export, values made up -->\n")


def process_xml(path, end_time_s, weld_step_s, cooling_max_step_s):
    blocks = "".join(
        '        <step_size_control loadcase_type="%d" precision_mode_type="0" solver_solution_type="%d">\n'
        '            <maximum_time_step dimension="9" unit="0" value="%r" is_automatic="%s"/>\n'
        "        </step_size_control>\n" % (lt, sol, v, auto)
        for lt, sol, v, auto in ((1, 2, weld_step_s, "true"), (2, 2, cooling_max_step_s, "false"),
                                 (2, 0, cooling_max_step_s, "false")))
    text = (HEADER + "<process_parameters synthetic=\"true\">\n    <process_definition_data>\n"
            '        <end_time dimension="9" unit="0" value="%r"/>\n'
            "        <time_steps_method>0</time_steps_method>\n"
            '        <fixed_time_step_value dimension="9" unit="0" value="%r"/>\n'
            "    </process_definition_data>\n    <step_size_controls>\n%s    </step_size_controls>\n"
            "</process_parameters>\n" % (float(end_time_s), float(weld_step_s), blocks))
    io.open(path, "w", encoding="utf-8", newline="\n").write(text)


def heat_source_xml(path, name, current_a, voltage_v, velocity_mm_s):
    text = (HEADER + "<wlWeldingParameter synthetic=\"true\">\n"
            '    <name display_name="%s" internal_name="%s"/>\n'
            "    <welding_parameter_data>\n"
            '        <velocity dimension="7" unit="1" value="%r"/>\n'
            '        <voltage dimension="20" unit="0" value="%r"/>\n'
            '        <current dimension="2" unit="0" value="%r"/>\n'
            "    </welding_parameter_data>\n</wlWeldingParameter>\n"
            % (name, name, float(velocity_mm_s), float(voltage_v), float(current_a)))
    io.open(path, "w", encoding="utf-8", newline="\n").write(text)
