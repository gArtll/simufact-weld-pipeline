# weldsim_tool prep/mesh_bead.tcl (copied from P0_C4_weld_prep_20260915\mesh_bead_c4.tcl, C4c micro-snap included).
# Scripted weld-bead mesh generator (HyperMesh hmbatch, plain Tcl file I/O; no marks, no *readfile/*writefile).
# Derived from P0_1b_bead_v2_20260914\mesh_bead.tcl (template, winding, BDF writer kept).
# Change vs v2: sections are NOT taken from reference section coordinates. They are placed at equal arc length
# along the weld root line produced by wl_v2.py (full weld-edge node polyline):
#   spans = floor(L / spacing), station s_i = i * L / spans, i = 0..spans
#   t  = unit tangent (central difference, h = 0.5 mm, clamped at the ends)
#   w  = web leg: unit( n_web x t ), n_web = (1,0,0); sign chosen so that w points into the web face
#        (towards the nearest web-face node that is off the edge line, i.e. the next node row above the weld edge)
#   pl = plate leg: unit( t x w ), sign chosen so that its X component has the sign of the weld side (neg: -X, pos: +X)
#   corners: root = line point; web toe = root + leg*w; plate toe = root + leg*pl
#   local node convention (same as the reference beads, ?????????.md / mesh_bead.tcl BEADS):
#     side neg (w001): local 1 = web toe, local 2 = plate toe;  side pos (w002): local 1 = plate toe, local 2 = web toe
#   nodes 3..6 from the Simufact quality-0 template (7 nodes, 3 quads per section, 3 HEXA per span)
# Parameters come from the file named by env WELD_PREP_PARAMS (written by weld_prep.py).
# SECTION_FRAME is a rule parameter, not a geometry-family name:
#   web_plane     : web normal + tangent, for a planar web edge
#   tube_on_plate : tube axial leg + radial plate leg, tangent changes around the circumference
#   set BEADS { {label side face_x root_full_csv out_bdf} ... } ; set LEG 5.4 ; set SPACING 3.33 ; set WEB_BDF ...
# Units mm. Inputs are read-only.

if {![info exists ::env(WELD_PREP_PARAMS)]} { puts "!! env WELD_PREP_PARAMS not set"; exit 2 }
source $::env(WELD_PREP_PARAMS)

set TEMPLATE {
    {0.0 0.0} {1.0 0.0} {0.0 1.0} {0.3943375 0.3943375} {0.5 0.5} {0.0 0.4999992} {0.4999992 0.0}
}
set QUADS {{3 4 2 5} {5 0 6 3} {3 6 1 4}}
set BUCKET 10.0

proc P {s} { puts $s; flush stdout }
proc vsub {a b} { list [expr {[lindex $a 0]-[lindex $b 0]}] [expr {[lindex $a 1]-[lindex $b 1]}] [expr {[lindex $a 2]-[lindex $b 2]}] }
proc vadd {a b} { list [expr {[lindex $a 0]+[lindex $b 0]}] [expr {[lindex $a 1]+[lindex $b 1]}] [expr {[lindex $a 2]+[lindex $b 2]}] }
proc vmul {a s} { list [expr {[lindex $a 0]*$s}] [expr {[lindex $a 1]*$s}] [expr {[lindex $a 2]*$s}] }
proc vdot {a b} { expr {[lindex $a 0]*[lindex $b 0]+[lindex $a 1]*[lindex $b 1]+[lindex $a 2]*[lindex $b 2]} }
proc vcross {a b} {
    lassign $a ax ay az; lassign $b bx by bz
    list [expr {$ay*$bz-$az*$by}] [expr {$az*$bx-$ax*$bz}] [expr {$ax*$by-$ay*$bx}]
}
proc vnorm {a} { set l [expr {sqrt([vdot $a $a])}]; if {$l == 0.0} { error "zero vector" }; vmul $a [expr {1.0/$l}] }
proc axis_vector {name} {
    if {$name eq "x"} { return {1.0 0.0 0.0} }
    if {$name eq "y"} { return {0.0 1.0 0.0} }
    return {0.0 0.0 1.0}
}

proc read_grid_star {path} {
    set f [open $path r]
    set nodes {}
    while {[gets $f line] >= 0} {
        if {[string range $line 0 4] ne "GRID*"} { continue }
        gets $f cont
        lappend nodes [list [expr {double([string trim [string range $line 40 55]])}] \
                            [expr {double([string trim [string range $line 56 71]])}] \
                            [expr {double([string trim [string range $cont 8 23]])}]]
    }
    close $f
    return $nodes
}

proc read_line_csv {path} {
    set f [open $path r]
    set pts {}
    while {[gets $f line] >= 0} {
        set q [split [string trim $line] ";"]
        if {[llength $q] != 5 || [lindex $q 1] ne "true"} { continue }
        lappend pts [list [expr {double([lindex $q 2])}] [expr {double([lindex $q 3])}] [expr {double([lindex $q 4])}]]
    }
    close $f
    return $pts
}

proc build_index {pts} {
    global BUCKET
    set idx [dict create]
    foreach p $pts { dict lappend idx [expr {int(floor([lindex $p 2] / $BUCKET))}] $p }
    return $idx
}

# point at arc length s on polyline pts with cumulative lengths cum
proc at_s {pts cum s} {
    set n [llength $pts]
    if {$s <= 0.0} { return [lindex $pts 0] }
    if {$s >= [lindex $cum end]} { return [lindex $pts end] }
    set lo 0; set hi [expr {$n-1}]
    while {$hi - $lo > 1} {
        set mid [expr {($lo+$hi)/2}]
        if {[lindex $cum $mid] <= $s} { set lo $mid } else { set hi $mid }
    }
    set c0 [lindex $cum $lo]; set c1 [lindex $cum $hi]
    set u [expr {$c1 > $c0 ? ($s-$c0)/($c1-$c0) : 0.0}]
    return [vadd [lindex $pts $lo] [vmul [vsub [lindex $pts $hi] [lindex $pts $lo]] $u]]
}

# direction into the web face: nearest web-face node that is > 1 mm away and not along the tangent
proc into_web {root t idx} {
    global BUCKET
    set b0 [expr {int(floor([lindex $root 2] / $BUCKET))}]
    set best 1.0e100; set bd {}
    for {set b [expr {$b0-2}]} {$b <= $b0+2} {incr b} {
        if {![dict exists $idx $b]} { continue }
        foreach q [dict get $idx $b] {
            set d [vsub $q $root]
            set l [expr {sqrt([vdot $d $d])}]
            if {$l < 1.0 || $l > 30.0} { continue }
            if {abs([vdot $d $t]) > 0.5*$l} { continue }
            if {$l < $best} { set best $l; set bd $d }
        }
    }
    return $bd
}

proc template_section {corners} {
    global TEMPLATE
    lassign [lindex $corners 0] x0 y0 z0
    lassign [lindex $corners 1] x1 y1 z1
    lassign [lindex $corners 2] x2 y2 z2
    set out {}
    foreach uv $TEMPLATE {
        lassign $uv u v
        lappend out [list [expr {$x0 + $u*($x1-$x0) + $v*($x2-$x0)}] [expr {$y0 + $u*($y1-$y0) + $v*($y2-$y0)}] [expr {$z0 + $u*($z1-$z0) + $v*($z2-$z0)}]]
    }
    return [lreplace $out 0 2 [lindex $corners 0] [lindex $corners 1] [lindex $corners 2]]
}

proc hexa_det_sign {X} {
    lassign [lindex $X 0] x0 y0 z0
    lassign [lindex $X 1] x1 y1 z1
    lassign [lindex $X 3] x3 y3 z3
    lassign [lindex $X 4] x4 y4 z4
    set ax [expr {$x1-$x0}]; set ay [expr {$y1-$y0}]; set az [expr {$z1-$z0}]
    set bx [expr {$x3-$x0}]; set by [expr {$y3-$y0}]; set bz [expr {$z3-$z0}]
    set cx [expr {$x4-$x0}]; set cy [expr {$y4-$y0}]; set cz [expr {$z4-$z0}]
    return [expr {($ay*$bz-$az*$by)*$cx + ($az*$bx-$ax*$bz)*$cy + ($ax*$by-$ay*$bx)*$cz}]
}

proc write_bead {path label sections info} {
    global QUADS CLOSED
    set f [open $path w]
    fconfigure $f -encoding ascii -translation crlf
    puts $f "\$ C4 weld_prep mesh_bead_c4.tcl scripted weld bead: $label"
    puts $f "\$ Units: mm; [llength $sections] sections; 7 nodes/section; 3 HEXA/span; $info"
    puts $f "SOL 101"
    puts $f "CEND"
    puts $f "BEGIN BULK"
    set nid 1
    foreach sec $sections {
        foreach p $sec {
            lassign $p x y z
            puts $f [format "%-8s%16d%16s%16.9e%16.9e%-8s" "GRID*" $nid "" $x $y "*"]
            puts $f [format "%-8s%16.9e" "*" $z]
            incr nid
        }
    }
    set flip 0
    set s0 [lindex $sections 0]; set s1 [lindex $sections 1]
    set X {}
    foreach k [lindex $QUADS 1] { lappend X [lindex $s0 $k] }
    foreach k [lindex $QUADS 1] { lappend X [lindex $s1 $k] }
    if {[hexa_det_sign $X] < 0.0} { set flip 1 }
    set eid 1
    set nspan [expr {$CLOSED ? [llength $sections] : [llength $sections]-1}]
    for {set i 0} {$i < $nspan} {incr i} {
        set a [expr {$i*7+1}]
        set b [expr {(($i+1) % [llength $sections])*7+1}]
        foreach q $QUADS {
            set ids {}
            if {$flip} {
                foreach k $q { lappend ids [expr {$b+$k}] }
                foreach k $q { lappend ids [expr {$a+$k}] }
            } else {
                foreach k $q { lappend ids [expr {$a+$k}] }
                foreach k $q { lappend ids [expr {$b+$k}] }
            }
            puts $f [format "%-8s%8d%8d%8d%8d%8d%8d%8d%8d%-8s" "CHEXA" $eid 1 [lindex $ids 0] [lindex $ids 1] [lindex $ids 2] [lindex $ids 3] [lindex $ids 4] [lindex $ids 5] "+"]
            puts $f [format "%-8s%8d%8d" "+" [lindex $ids 6] [lindex $ids 7]]
            incr eid
        }
    }
    puts $f "ENDDATA"
    close $f
    P [format "WROTE %s: sections=%d nodes=%d CHEXA=%d winding_flip=%d" $path [llength $sections] [expr {$nid-1}] [expr {$eid-1}] $flip]
}

P "Reading web mesh $WEB_BDF ..."
set web [read_grid_star $WEB_BDF]
P "web nodes=[llength $web] leg=$LEG spacing=$SPACING"
if {![info exists SNAP_TOL]} { set SNAP_TOL 0.0 }
set plate {}
if {[info exists PLATE_BDF]} { set plate [read_grid_star $PLATE_BDF] }
set bidx [build_index [concat $web $plate]]
P "base nodes for micro-snap: web=[llength $web] plate=[llength $plate] SNAP_TOL=$SNAP_TOL mm"

proc nearest_base {p idx} {
    global BUCKET
    lassign $p x y z
    set b0 [expr {int(floor($z / $BUCKET))}]
    set best 1.0e100; set bp {}
    for {set b [expr {$b0-1}]} {$b <= $b0+1} {incr b} {
        if {![dict exists $idx $b]} { continue }
        foreach q [dict get $idx $b] {
            lassign $q a c d
            set d2 [expr {($x-$a)*($x-$a)+($y-$c)*($y-$c)+($z-$d)*($z-$d)}]
            if {$d2 < $best} { set best $d2; set bp $q }
        }
    }
    return [list $bp [expr {sqrt($best)}]]
}

foreach spec $BEADS {
    lassign $spec label side facex rootcsv outbdf
    set sgn [expr {$side eq "neg" ? -1.0 : 1.0}]
    set pts [read_line_csv $rootcsv]
    set cum [list 0.0]
    for {set k 1} {$k < [llength $pts]} {incr k} {
        set d [vsub [lindex $pts $k] [lindex $pts [expr {$k-1}]]]
        lappend cum [expr {[lindex $cum end] + sqrt([vdot $d $d])}]
    }
    set L [lindex $cum end]
    set spans [expr {int(floor($L / $SPACING))}]
    set ds [expr {$L / $spans}]
    set face {}
    foreach p $web { if {abs([lindex $p 0] - $facex) < 1.0e-6} { lappend face $p } }
    set fidx [build_index $face]
    set sections {}
    set nofallback 0; set fallback 0
    set prevw {}
    set maxdev 0.0
    set laststation [expr {$CLOSED ? $spans-1 : $spans}]
    for {set i 0} {$i <= $laststation} {incr i} {
        set s [expr {$i * $ds}]
        set root [at_s $pts $cum $s]
        set sa [expr {$s - 0.5 < 0.0 ? 0.0 : $s - 0.5}]
        set sb [expr {$s + 0.5 > $L ? $L : $s + 0.5}]
        set t [vnorm [vsub [at_s $pts $cum $sb] [at_s $pts $cum $sa]]]
        if {$SECTION_FRAME eq "tube_on_plate"} {
            set w [axis_vector $EDGE_AXIS]
            set radial [vsub $root $EDGE_CENTER]
            set av [axis_vector $EDGE_AXIS]
            set radial [vsub $radial [vmul $av [vdot $radial $av]]]
            set pl [vnorm $radial]
            incr nofallback
        } else {
            set w [vnorm [vcross {1.0 0.0 0.0} $t]]
            set dw [into_web $root $t $fidx]
            if {[llength $dw]} {
                if {[vdot $w $dw] < 0.0} { set w [vmul $w -1.0] }
                incr nofallback
            } else {
                if {[llength $prevw] && [vdot $w $prevw] < 0.0} { set w [vmul $w -1.0] }
                incr fallback
            }
            set pl [vnorm [vcross $t $w]]
            if {[lindex $pl 0] * $sgn < 0.0} { set pl [vmul $pl -1.0] }
        }
        set prevw $w
        set webtoe [vadd $root [vmul $w $LEG]]
        set platetoe [vadd $root [vmul $pl $LEG]]
        set dev [expr {max(abs([lindex $root 0]-$facex), abs([lindex $webtoe 0]-$facex))}]
        if {$dev > $maxdev} { set maxdev $dev }
        if {$side eq "neg"} {
            set corners [list $root $webtoe $platetoe]
        } else {
            set corners [list $root $platetoe $webtoe]
        }
        lappend sections [template_section $corners]
    }
    # C4c micro-snap: contact-face nodes (local 0 root, 1/2 toes, 5/6 leg mid nodes) closer than SNAP_TOL to a base
    # node (web or plate) but not already coincident are moved onto that node exactly.
    set nsnap 0; set maxmove 0.0; set snaplog {}
    for {set i 0} {$i < [llength $sections]} {incr i} {
        set sec [lindex $sections $i]
        foreach k {0 1 2 5 6} {
            set p [lindex $sec $k]
            lassign [nearest_base $p $bidx] q d
            if {$d > 1.0e-9 && $d < $SNAP_TOL} {
                set sec [lreplace $sec $k $k $q]
                incr nsnap
                if {$d > $maxmove} { set maxmove $d }
                lappend snaplog [format "SNAPNODE %s section=%d local=%d z=%.3f move=%.6f mm" $label $i $k [lindex $p 2] $d]
            }
        }
        set sections [lreplace $sections $i $i $sec]
    }
    foreach s $snaplog { P $s }
    P [format "SNAP %s tol=%.4f mm snapped=%d max_move=%.6f mm" $label $SNAP_TOL $nsnap $maxmove]
    P [format "BEAD %s side=%s face_x=%.4f root_len=%.4f mm spans=%d sections=%d spacing=%.6f mm web_dir_found=%d fallback=%d max|x-face| root/webtoe=%.3g" \
        $label $side $facex $L $spans [llength $sections] $ds $nofallback $fallback $maxdev]
    write_bead $outbdf $label $sections [format "root_len %.4f mm, spans %d, spacing %.6f mm, leg %.3f mm, side %s" $L $spans $ds $LEG $side]
}
P "DONE"
