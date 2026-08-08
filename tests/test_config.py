from gdsx import config


def test_via_pairs_reference_real_routing_layers(tech):
    names = [rl.name for rl in tech.routing]
    for via in tech.vias:
        assert via.below in names and via.above in names
        # a via must bridge exactly one step of the stack
        assert names.index(via.above) - names.index(via.below) == 1


def test_layer_numbers_match_the_sample_gds(sample, tech):
    present = set(sample.layer_index)
    for rl in tech.routing:
        assert rl.drawing in present, f"{rl.name} drawing layer missing from sample"
    for via in tech.vias:
        assert via.layer in present, f"{via.name} missing from sample"


def test_golden_layer_numbers(tech):
    """Locked for sky130A"""
    assert tech.layer("li1").drawing == (67, 20)
    assert tech.layer("met1").drawing == (68, 20)
    assert [(v.name, v.layer) for v in tech.vias] == [
        ("mcon", (67, 44)),
        ("via", (68, 44)),
        ("via2", (69, 44)),
        ("via3", (70, 44)),
        ("via4", (71, 44)),
    ]


def test_fill_and_tap_are_not_logic():
    tech = config.load()
    assert tech.is_logic_cell("sky130_fd_sc_hd__nand2_2")
    assert not tech.is_logic_cell("sky130_fd_sc_hd__decap_3")
    assert not tech.is_logic_cell("sky130_fd_sc_hd__tapvpwrvgnd_1")
    assert not tech.is_logic_cell("VIA_M1M2_PR")
