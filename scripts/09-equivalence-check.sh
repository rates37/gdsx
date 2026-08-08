#!/bin/bash

# check equivalence script between the sample.v (what the design acually is)
# and adder_demo.generic.v (what my code exported the .gds file as)
# just used as a sanity checker

cat > /tmp/eq.ys <<'EOF'
read_verilog samples/sample.v
prep -top adder_demo -flatten
async2sync
design -stash gold

read_verilog /tmp/prims.v out/adder_demo.generic.v
prep -top adder_demo -flatten
async2sync
design -stash gate

design -copy-from gold -as gold adder_demo
design -copy-from gate -as gate adder_demo
miter -equiv -flatten -make_assert gold gate miter
hierarchy -top miter
sat -prove-asserts -seq 12 -set-init-zero -show-inputs -show-outputs miter
EOF

yosys /tmp/eq.ys 
