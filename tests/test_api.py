"""The Python<->JS contract

Every assertion here is about the shape of what crosses the boundary, not about
the analysis behind it, that is covered by the tests for each module.
"""

from __future__ import annotations

import json
import pathlib

import pytest
from gdsx import api

SAMPLES = pathlib.Path(__file__).resolve().parents[1] / "samples"
SAMPLE = SAMPLES / "sample.gds"
PUZZLE = SAMPLES / "puzzle.gds"


def unwrap(payload: str):
    """The `data` of a successful envelope, checking the envelope itself"""
    found = json.loads(payload)
    assert found["schema_version"] == api.SCHEMA_VERSION
    assert found["ok"] is True, found.get("error")
    return found["data"]


def failure(payload: str) -> dict:
    found = json.loads(payload)
    assert found["schema_version"] == api.SCHEMA_VERSION
    assert found["ok"] is False, found.get("data")
    return found["error"]


@pytest.fixture()
def handle():
    """A handle on sample.gds, closed afterwards so the registry stays clean"""
    got = unwrap(api.open_design(SAMPLE.read_bytes()))["handle"]
    yield got
    api.close(got)


@pytest.fixture()
def netlist_handle(handle):
    """A handle with no layout behind it, from the extracted netlist"""
    got = unwrap(api.load_netlist(json.dumps(unwrap(api.extract(handle))["netlist"])))
    yield got["handle"]
    api.close(got["handle"])


#! handles


def test_open_design_reports_the_layout_without_extracting():
    data = unwrap(api.open_design(SAMPLE.read_bytes()))
    assert data["top"] == "adder_demo"
    assert data["dbu"] == 0.001
    assert data["instance_count"] > data["cell_count"] > 0
    assert data["has_layout"] is True
    api.close(data["handle"])


def test_open_design_takes_a_tech_config_as_json():
    from gdsx import config

    raw = json.loads(config.DEFAULT_JSON_CONFIG.read_text())
    data = unwrap(api.open_design(SAMPLE.read_bytes(), json.dumps(raw)))
    assert data["top"] == "adder_demo"
    api.close(data["handle"])


def test_handles_are_opaque_and_closing_one_forgets_it(handle):
    assert handle in unwrap(api.handles())["handles"]
    unwrap(api.close(handle))
    assert handle not in unwrap(api.handles())["handles"]
    assert failure(api.inspect(handle))["code"] == "bad_handle"
    # the fixture's own close must still answer, not raise
    assert failure(api.close(handle))["code"] == "bad_handle"


def test_every_endpoint_rejects_an_unknown_handle():
    for call in (
        api.extract,
        api.instances,
        api.nets,
        api.inspect,
        api.pins,
        api.placement,
        api.registers,
        api.guards,
        api.ports,
        api.analyse,
        api.fsm,
        api.idioms,
        api.normalise,
        api.sim_compile,
        api.claim_vocabulary,
    ):
        assert failure(call("design-nope"))["code"] == "bad_handle", call.__name__


#! extraction and the round trip


def test_extract_round_trips_through_load_netlist(handle):
    data = unwrap(api.extract(handle))
    assert data["summary"]["instance_count"] == len(data["netlist"]["instances"])

    other = unwrap(api.load_netlist(json.dumps(data["netlist"])))["handle"]
    try:
        assert unwrap(api.instances(other)) == unwrap(api.instances(handle))
        assert unwrap(api.nets(other)) == unwrap(api.nets(handle))
    finally:
        api.close(other)


def test_cache_key_is_stable_and_sensitive_to_schema_version():
    key = unwrap(api.cache_key(SAMPLE.read_bytes()))["key"]
    assert key == unwrap(api.cache_key(SAMPLE.read_bytes()))["key"]
    assert key.endswith(f"-{api.SCHEMA_VERSION}")
    assert key != unwrap(api.cache_key(PUZZLE.read_bytes()))["key"]


def test_extract_reports_progress(handle):
    """`progress` is called `(stage, done, total)` all the way through the
    real extraction stages (the tracer walking the routing stack, not one
    opaque jump) so watching re-extraction sees it move.
    """
    seen: list[tuple[str, int, int]] = []
    unwrap(api.extract(handle, progress=lambda *args: seen.append(args)))

    # the outer shape: opens first, done last, real work in between
    stages = [stage for stage, _, _ in seen]
    assert stages[0] == "open"
    assert stages[-1] == "done"
    assert set(stages) >= {"open", "trace", "vias", "resolve", "serialise", "done"}

    # every report is in bounds, and a stage's own reports climb to its own total
    by_stage: dict[str, list[tuple[int, int]]] = {}
    for stage, done, total in seen:
        assert 0 <= done <= total
        by_stage.setdefault(stage, []).append((done, total))
    for stage, reports in by_stage.items():
        totals = {total for _, total in reports}
        assert len(totals) == 1, f"{stage} changed its own total mid-stream"
        dones = [done for done, _ in reports]
        assert dones == sorted(dones), f"{stage} went backwards"
        assert dones[-1] == totals.pop(), f"{stage} never reached its own total"


def test_a_netlist_handle_has_no_layout(netlist_handle):
    for call in (api.inspect, api.pins, api.placement):
        assert failure(call(netlist_handle))["code"] == "no_layout", call.__name__
    # but everything downstream of extraction still works
    assert unwrap(api.registers(netlist_handle))["registers"]


def test_load_netlist_rejects_rubbish():
    assert failure(api.load_netlist("{not json"))["code"] == "bad_json"
    assert failure(api.load_netlist('{"nope": 1}'))["code"] == "bad_json"


#! the 4b views, and the three traps


def test_instance_view_holds_the_three_traps(handle):
    views = {v["name"]: v for v in unwrap(api.instances(handle))}
    flop = next(v for v in views.values() if v["is_sequential"])

    # trap 1: the full cell name and the Liberty base name are different strings
    assert flop["cell"].startswith("sky130_fd_sc_hd__")
    assert flop["base_cell"] in flop["cell"]
    assert flop["cell"] != flop["base_cell"]

    # trap 2: connections may omit pins, and an absent pin is not a zero
    library = {"D", "Q", "CLK", "RESET_B", "VPWR", "VGND", "VPB", "VNB"}
    assert library - set(flop["connections"]), "no unconnected pin to test with"
    assert all(isinstance(net, str) for net in flop["connections"].values())

    assert flop["functions"] == {} or all(
        isinstance(v, str) for v in flop["functions"].values()
    )
    assert flop["bbox"] is None


def test_net_view_classifies_ports_drivers_and_leaves(handle):
    views = {v["name"]: v for v in unwrap(api.nets(handle))}
    port = next(v for v in views.values() if v["is_port"] == "input")
    assert port["driver"] is None and port["leaf"] == "primary_in"

    driven = next(v for v in views.values() if v["driver"] is not None)
    assert driven["driver"]["direction"] == "output"
    assert set(driven["driver"]) == {"instance", "pin", "cell", "direction"}
    assert all(r["direction"] == "input" for r in driven["readers"])


def test_views_can_be_asked_for_by_name(handle):
    every = unwrap(api.nets(handle))
    wanted = [every[0]["name"], every[-1]["name"]]
    assert unwrap(api.nets(handle, json.dumps(wanted))) == [every[0], every[-1]]
    assert failure(api.nets(handle, '["nope"]'))["code"] == "unknown_net"
    assert failure(api.instances(handle, '["nope"]'))["code"] == "unknown_instance"


#! cones


def walk(node: dict):
    yield node
    for kid in node["children"]:
        yield from walk(kid)


def test_cone_marks_truncation_apart_from_leaves(handle):
    output = next(
        v["name"] for v in unwrap(api.nets(handle)) if v["is_port"] == "output"
    )
    shallow = unwrap(api.cone(handle, output, depth=1))
    truncated = [n for n in walk(shallow) if n["truncated"]]
    assert truncated, "a depth-1 cone on an output must stop somewhere"
    for node in truncated:
        assert node["leaf"] is None, "a truncated node is not a leaf"
        assert node["gate"] is not None, "something drives it, we just stopped"

    deep = unwrap(api.cone(handle, output, depth=99))
    leaves = [n for n in walk(deep) if n["leaf"] is not None]
    assert leaves and all(not n["truncated"] and not n["children"] for n in leaves)
    assert {n["leaf"] for n in leaves} <= {
        "primary_in",
        "const0",
        "const1",
        "flop_q",
        "undriven",
    }
    assert all(n["gate"] is None for n in leaves), "a leaf has nothing driving it"


def test_cone_answers_in_both_directions_with_one_shape(handle):
    nets = unwrap(api.nets(handle))
    inp = next(v["name"] for v in nets if v["is_port"] == "input")
    out = next(v["name"] for v in nets if v["is_port"] == "output")
    shape = {"net", "gate", "pins", "leaf", "children", "truncated"}
    for node in (
        unwrap(api.cone(handle, out, depth=2)),
        unwrap(api.cone(handle, inp, depth=2, direction="out")),
    ):
        assert all(set(found) == shape for found in walk(node))


def test_cone_rejects_an_unknown_net_and_a_bad_direction(handle):
    net = unwrap(api.nets(handle))[0]["name"]
    assert failure(api.cone(handle, "nope"))["code"] == "unknown_net"
    assert failure(api.cone(handle, net, direction="sideways"))["code"] == "bad_json"


#! requirements: the cone walker's flatten button


def test_requirements_flattens_a_pure_and_tree_into_forced_leaves(handle):
    """An AND of two flop outputs: both leaves come back forced, no choices."""
    inst = next(
        v for v in unwrap(api.instances(handle)) if v["base_cell"] == "and2"
    )
    out_pin = next(iter(inst["functions"]))
    net = inst["connections"][out_pin]
    found = unwrap(api.requirements(handle, net, value=1))
    assert found["net"] == net and found["value"] == 1
    assert found["consistent"] is True
    assert found["conflicts"] == []
    assert not found["choices"], "a pure AND has nothing left unresolved"
    assert {leaf["value"] for leaf in found["leaves"]} == {1}
    assert len(found["leaves"]) >= 2


def test_requirements_reports_an_or_as_a_choice_not_a_forced_leaf(handle):
    inst = next(
        (v for v in unwrap(api.instances(handle)) if v["base_cell"] == "or2"), None
    )
    if inst is None:
        pytest.skip("sample.gds has no or2 to check")
    out_pin = next(iter(inst["functions"]))
    net = inst["connections"][out_pin]
    found = unwrap(api.requirements(handle, net, value=1))
    assert found["choices"], "an OR asked for 1 forces neither input"
    forced_nets = {leaf["net"] for leaf in found["leaves"]}
    for choice in found["choices"]:
        for option in choice["options"]:
            for literal in option["literals"]:
                assert literal["net"] not in forced_nets, (
                    "a literal that is part of a choice is not also forced"
                )


def test_requirements_rejects_an_unknown_net_and_a_bad_value(handle):
    net = unwrap(api.nets(handle))[0]["name"]
    assert failure(api.requirements(handle, "nope"))["code"] == "unknown_net"
    assert failure(api.requirements(handle, net, value=2))["code"] == "bad_json"


#! flop_d_net: the cone walker's step-through-flop button


def test_flop_d_net_is_one_cycle_earlier_than_the_q_net(handle):
    from gdsx.core.context import Design

    design = Design.open(SAMPLE.read_bytes())
    graph = design.graph
    flop = sorted(graph.seq)[0]
    q_net = graph.by_name[flop].connections["Q"]

    found = unwrap(api.flop_d_net(handle, q_net))
    assert found["instance"] == flop
    assert found["net"] == graph.d_pin(flop)


def test_flop_d_net_rejects_a_net_no_flop_drives(handle):
    net = next(
        v["name"] for v in unwrap(api.nets(handle)) if v["leaf"] != "flop_q"
    )
    assert failure(api.flop_d_net(handle, net))["code"] == "not_a_flop"
    assert failure(api.flop_d_net(handle, "nope"))["code"] == "unknown_net"


#! sub_netlist


def test_sub_netlist_carves_out_the_chosen_instances(handle):
    names = [v["name"] for v in unwrap(api.instances(handle))]
    chosen = names[: max(1, len(names) // 4)]

    data = unwrap(api.sub_netlist(handle, json.dumps(chosen)))
    got_names = {inst["name"] for inst in data["netlist"]["instances"]}
    assert got_names == set(chosen)
    assert data["summary"]["instance_count"] == len(chosen)
    assert data["summary"]["net_count"] == len(data["netlist"]["nets"])
    assert data["summary"]["port_count"] == len(data["netlist"]["ports"])


def test_sub_netlist_round_trips_into_load_netlist(handle):
    names = [v["name"] for v in unwrap(api.instances(handle))]
    chosen = names[: max(1, len(names) // 4)]
    data = unwrap(api.sub_netlist(handle, json.dumps(chosen), name="a_slice"))
    assert data["netlist"]["top"] == "a_slice"

    loaded = unwrap(api.load_netlist(json.dumps(data["netlist"])))
    assert loaded["top"] == "a_slice"
    assert loaded["has_layout"] is False
    api.close(loaded["handle"])


def test_sub_netlist_rejects_an_unknown_instance(handle):
    assert (
        failure(api.sub_netlist(handle, json.dumps(["nope"])))["code"]
        == "unknown_instance"
    )


def test_sub_netlist_rejects_an_empty_or_malformed_list(handle):
    assert failure(api.sub_netlist(handle, json.dumps([])))["code"] == "bad_json"
    assert failure(api.sub_netlist(handle, json.dumps({"a": 1})))["code"] == "bad_json"


def test_sub_netlist_rejects_an_unknown_handle():
    assert (
        failure(api.sub_netlist("design-nope", json.dumps(["x"])))["code"]
        == "bad_handle"
    )


#! layout


def test_layout_of_a_chosen_block_stays_within_it(handle):
    names = [v["name"] for v in unwrap(api.instances(handle))]
    chosen = names[: max(1, len(names) // 4)]

    got = unwrap(api.layout(handle, json.dumps(chosen)))
    gate_labels = {n["label"] for n in got["nodes"] if n["kind"] == "gate"}
    assert gate_labels == set(chosen)
    assert got["n_layers"] >= 1
    for node in got["nodes"]:
        assert node["x"] == node["layer"] * 160
        for edge in got["edges"]:
            assert isinstance(edge["feedback"], bool)


def test_layout_with_no_instances_lays_out_the_whole_small_design(handle):
    whole = unwrap(api.layout(handle))
    by_instances = unwrap(
        api.layout(
            handle, json.dumps([v["name"] for v in unwrap(api.instances(handle))])
        )
    )
    assert {n["id"] for n in whole["nodes"]} == {n["id"] for n in by_instances["nodes"]}


def test_layout_rejects_an_unknown_instance(handle):
    assert (
        failure(api.layout(handle, json.dumps(["nope"])))["code"] == "unknown_instance"
    )


def test_layout_rejects_an_empty_or_malformed_list(handle):
    assert failure(api.layout(handle, json.dumps([])))["code"] == "bad_json"
    assert failure(api.layout(handle, json.dumps({"a": 1})))["code"] == "bad_json"


def test_layout_reports_too_many_nodes_over_the_cap(handle, monkeypatch):
    from gdsx.analysis import layout as layout_module

    monkeypatch.setattr(layout_module, "MAX_NODES", 1)
    found = failure(api.layout(handle))
    assert found["code"] == "too_many_nodes"
    assert found["detail"]["count"] > 1


#! analyses


def test_every_analysis_answers_with_json(handle):
    assert unwrap(api.inspect(handle))["top"] == "adder_demo"
    assert unwrap(api.pins(handle))["cells"]
    assert unwrap(api.placement(handle))["units"] == "um"
    assert unwrap(api.registers(handle))["registers"][0]["description"]
    assert "ungated" in unwrap(api.guards(handle))
    assert unwrap(api.ports(handle, cycles=8))["inputs"]
    assert "registers" in unwrap(api.analyse(handle))
    assert "machines" in unwrap(api.fsm(handle))
    assert "by_idiom" in unwrap(api.idioms(handle))
    assert unwrap(api.normalise(handle))["netlist"]["top"]


def test_guard_groups_survive_json(handle):
    # `Guards.groups()` is keyed by a tuple of conditions, which json cannot hold
    for group in unwrap(api.guards(handle))["groups"]:
        assert set(group) == {"condition", "flops"}
        assert all(set(c) == {"net", "value"} for c in group["condition"])


def test_fsm_transitions_survive_json(handle):
    # `transitions` is keyed by a (state, inputs) tuple and `moore_outputs` by an
    # int; both would be lost or mangled by json.dumps if left as dicts
    for machine in unwrap(api.fsm(handle))["machines"]:
        assert all(set(t) == {"state", "input", "next"} for t in machine["transitions"])
        assert all(set(m) == {"state", "outputs"} for m in machine["moore_outputs"])


def test_fsm_rejects_an_unknown_register(handle):
    assert failure(api.fsm(handle, "nope"))["code"] == "unknown_register"


def test_truth_table_needs_no_handle():
    data = unwrap(api.truth_table("sky130_fd_sc_hd__nand2_1"))
    assert data["base_cell"] == "nand2" and data["generic"] == "NAND2"
    assert len(data["rows"]) == 4
    assert [r["outputs"]["Y"] for r in data["rows"]] == [1, 1, 1, 0]
    assert [r["inputs"] for r in data["rows"]][-1] == {"A": 1, "B": 1}

    assert failure(api.truth_table("nope"))["code"] == "unknown_cell"
    assert (
        failure(api.truth_table("sky130_fd_sc_hd__dfrtp_2"))["code"] == "unknown_cell"
    )


#! constraints


def test_constraints_system_carries_bounds_the_hits_form_cannot():
    """`constraints_build` can only say "exactly k of these"; a spacing rule
    is `0 <= a + b <= 1` per forbidden pair. Both bounds must survive the
    round trip, and `variables` is the union of every row's elements.
    """
    rows = [
        {"name": "epoch 0", "elements": [0, 1, 2, 3], "lb": 2, "ub": 2},
        {"name": "spacing 0,1", "elements": [0, 1], "lb": 0, "ub": 1},
    ]
    data = unwrap(api.constraints_system(json.dumps(rows), json.dumps(["epoch 0"])))
    assert data["variables"] == [0, 1, 2, 3]
    assert [(c["lb"], c["ub"]) for c in data["constraints"]] == [(2, 2), (0, 1)]
    assert data["unconstrained"] == []

    solutions = unwrap(api.constraints_solve(json.dumps(data), "dfs", 50))
    assert solutions["capped"] is False
    assert sorted(map(tuple, solutions["solutions"])) == [(0, 2), (0, 3), (1, 2), (1, 3), (2, 3)]


def test_constraints_system_names_a_watched_element_with_no_row():
    rows = [{"name": "epoch 0", "elements": [0, 1], "lb": 1, "ub": 1}]
    watched = ["epoch 0", "the trap nobody wrote a row for"]
    data = unwrap(api.constraints_system(json.dumps(rows), json.dumps(watched)))
    assert data["unconstrained"] == ["the trap nobody wrote a row for"]


def test_constraints_system_rejects_rubbish():
    assert failure(api.constraints_system("not json", "[]"))["code"] == "bad_json"


#! simulation


def test_sim_run_traces_the_watched_nets(handle):
    vectors = [{"clk": 0}, {"clk": 1}, {"clk": 0}]
    data = unwrap(api.sim_run(handle, json.dumps(vectors)))
    assert data["cycles"] == 3
    # watch defaults to the ports: a design this size has too many nets to trace
    assert set(data["nets"]) == {
        v["name"] for v in unwrap(api.nets(handle)) if v["is_port"]
    }
    assert all(len(v) == 3 for v in data["nets"].values())
    assert all(set(v) <= {0, 1} for v in data["flops"].values())

    watched = sorted(data["nets"])[:1]
    picked = unwrap(api.sim_run(handle, json.dumps(vectors), json.dumps(watched)))
    assert set(picked["nets"]) == set(watched)


def test_sim_run_resets_between_calls(handle):
    vectors = [{"clk": c % 2} for c in range(8)]
    first = unwrap(api.sim_run(handle, json.dumps(vectors)))
    assert first == unwrap(api.sim_run(handle, json.dumps(vectors)))


def test_sim_run_rejects_rubbish(handle):
    assert failure(api.sim_run(handle, "{"))["code"] == "bad_json"
    assert failure(api.sim_run(handle, '{"clk": 0}'))["code"] == "bad_json"
    assert failure(api.sim_run(handle, "[]", '["nope"]'))["code"] == "unknown_net"


def test_sim_compile_returns_a_runnable_tape(handle):
    """The payload the JS executor runs, checked as a contract not as a number

    Values are 0 or 1 only and operand slots are net ids or -1, so the JS side
    can index straight into a typed array without validating first.
    """
    data = unwrap(api.sim_compile(handle))
    assert data["tape_version"] >= 1
    assert data["n_ops"] * 6 == len(data["ops"])
    assert all(-1 <= value < data["n_nets"] for value in data["ops"])
    assert len(data["flops"]) == data["n_flops"] == len(data["flop_names"])
    assert all(value in (0, 1) for _, value in data["consts"])
    assert set(data["inputs"]) <= set(data["names"].values())


def test_render_bundle_answers_in_bytes_not_an_envelope(handle):
    from gdsx.render import RenderBundle

    raw = api.render_bundle(handle)
    assert isinstance(raw, bytes), "render payloads are bytes, not JSON strings"
    bundle = RenderBundle.unpack(raw)
    assert bundle.header["schema_version"] >= 1
    assert bundle.header["layers"], "sample.gds should have routing layers"


def test_render_bundle_needs_a_layout(netlist_handle):
    raw = api.render_bundle(netlist_handle)
    error = failure(raw.decode("utf-8"))
    assert error["code"] == "no_layout"


#! the real design


@pytest.mark.slow
def test_the_puzzle_extracts_and_analyses_through_the_facade():
    handle = unwrap(api.open_design(PUZZLE.read_bytes()))["handle"]
    try:
        data = unwrap(api.extract(handle))
        assert data["summary"]["instance_count"] == 728
        assert unwrap(api.instances(handle))[0]["cell"].startswith("sky130")
        # `success` is a port driven by a flop, not a net of combinational
        # logic, so its fan-in root is a leaf and the D cone is a cycle back
        success = unwrap(api.cone(handle, "success", depth=3))
        assert success["leaf"] == "flop_q" and success["gate"] is None
        view = unwrap(api.nets(handle, '["success"]'))[0]
        assert view["driver"]["direction"] == "output"
        assert view["driver"]["cell"].startswith("sky130_fd_sc_hd__dfrtp")
    finally:
        api.close(handle)


#! notebook claims


def _plan(handle, claim: dict):
    return unwrap(api.claim_plan(handle, json.dumps(claim)))


def _a_flop(handle) -> str:
    return unwrap(api.claim_vocabulary(handle))["flops"][0]


def test_claim_vocabulary_is_what_the_forms_offer(handle):
    unwrap(api.extract(handle))
    found = unwrap(api.claim_vocabulary(handle))
    assert "structural" in found["kinds"] and len(found["kinds"]) == 8
    assert "counter" in found["roles"]
    assert "latches-high" in found["events"]
    assert "count" in found["measures"]
    assert found["flops"] and found["inputs"]


def test_a_structural_claim_comes_back_settled(handle):
    unwrap(api.extract(handle))
    net = next(
        view["name"]
        for view in unwrap(api.nets(handle))
        if view["driver"] is not None
    )
    driver = unwrap(api.nets(handle, json.dumps([net])))[0]["driver"]

    plan = _plan(handle, {"kind": "structural", "net": net, "cell": driver["cell"]})
    assert plan["job"] is None
    assert plan["verdict"]["kind"] == "PROVEN"
    assert plan["verdict"]["method"] == "structural"
    assert plan["call"].startswith("gdsx.")


def test_a_functional_claim_comes_back_as_a_job_with_two_aligned_slices(handle):
    unwrap(api.extract(handle))
    flop = _a_flop(handle)
    plan = _plan(handle, {"kind": "function", "flop": flop, "expression": "1"})
    assert plan["verdict"] is None
    job = plan["job"]
    assert job["engine"] == "combinational" and job["check"] == "equal"
    # The contract that makes a counterexample decodable on both sides.
    assert job["design"]["free"] == job["claim"]["free"]
    assert len(job["design"]["ops"]) % 6 == 0
    assert job["design"]["n_values"] > 0


def test_a_constraint_claim_carries_its_parsed_predicate(handle):
    unwrap(api.extract(handle))
    vocabulary = unwrap(api.claim_vocabulary(handle))
    port = vocabulary["inputs"][0]
    output = vocabulary["outputs"][0]

    plan = _plan(
        handle,
        {"kind": "constraint", "output": output, "predicate": f"count({port}) == 4"},
    )
    predicate = plan["job"]["predicate"]
    assert predicate["node"] == "term"
    assert predicate["measure"] == "count" and predicate["port"] == port
    assert predicate["value"] == 4 and predicate["of"] == []


def test_claim_errors_are_form_errors_not_verdicts(handle):
    unwrap(api.extract(handle))
    flop = _a_flop(handle)

    assert failure(api.claim_plan(handle, '{"kind": "vibes"}'))["code"] == "bad_claim"
    assert (
        failure(api.claim_plan(handle, json.dumps({"kind": "structural", "net": "n_nope", "cell": "nand2"})))["code"]
        == "unknown_net"
    )
    assert (
        failure(api.claim_plan(handle, json.dumps({"kind": "function", "flop": flop, "expression": "& &"})))["code"]
        == "bad_expression"
    )
    assert (
        failure(api.claim_plan(handle, json.dumps({"kind": "function", "flop": "nope", "expression": "1"})))["code"]
        == "unknown_flop"
    )
    assert failure(api.claim_plan(handle, "not json"))["code"] == "bad_json"
    assert failure(api.claim_plan(handle, "[1, 2]"))["code"] == "bad_json"


def test_no_verdict_from_this_side_is_ever_likely(handle):
    """The boundary's half of the rule the whole mechanic rests on

    `LIKELY` is produced by sampling and sampling happens in the evaluator. If
    one ever appeared in an envelope, something on this side would have started
    guessing.
    """
    unwrap(api.extract(handle))
    flop = _a_flop(handle)
    vocabulary = unwrap(api.claim_vocabulary(handle))
    output = vocabulary["outputs"][0]
    port = vocabulary["inputs"][0]

    for claim in (
        {"kind": "structural", "net": output, "cell": "nand2"},
        {"kind": "support", "flop": flop, "leaves": []},
        {"kind": "support", "flop": flop, "leaves": [], "sense": "functional"},
        {"kind": "function", "flop": flop, "expression": "1"},
        {"kind": "role", "group": [flop], "role": "counter", "width": 1},
        {"kind": "invariant", "net": output, "value": 1, "condition": port},
        {"kind": "requirement", "output": output, "value": 1, "flop": flop, "flop_value": 1},
        {"kind": "timing", "net": output, "event": "high", "cycles": [1]},
        {"kind": "constraint", "output": output, "predicate": f"count({port}) == 1"},
    ):
        plan = _plan(handle, claim)
        assert (plan["verdict"] is None) != (plan["job"] is None), claim["kind"]
        if plan["verdict"] is not None:
            assert plan["verdict"]["kind"] != "LIKELY", claim["kind"]
