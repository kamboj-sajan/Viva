from collections import Counter

from viva.questions import MIX, leaks_location, plan_types


def test_plan_types_follows_mix_and_area_kinds():
    kinds = ["core_flow", "component", "failure_handling", "data_model", "component", "core_flow", "deployment", "component"]
    plan = plan_types(kinds)
    assert [len(p) for p in plan] == [3, 3, 3, 3, 2, 2, 2, 2]
    total = sum(len(p) for p in plan)
    mix = Counter(t for p in plan for t in p)
    for t, share in MIX.items():
        assert abs(mix[t] - share * total) <= 1, (t, mix[t], share * total)
    assert plan[2][0] == "failure_modes"  # failure-handling area leads with failure questions
    assert plan[6][0] in ("scaling", "design_decision")  # deployment area


def test_plan_types_small_repo():
    plan = plan_types(["core_flow", "component", "failure_handling", "deployment"])
    assert sum(len(p) for p in plan) == 10
    assert Counter(t for p in plan for t in p) == {"how_it_works": 4, "failure_modes": 3, "design_decision": 2, "scaling": 1}


def test_leaks_location():
    paths = ["src/net/retry.py", "Dockerfile", "web/index.ts"]
    assert leaks_location("What happens in retry.py when the server times out?", paths)
    assert leaks_location("Walk me through line 42.", paths)
    assert leaks_location("Why does index.ts re-export everything?", paths)
    assert not leaks_location("How do you retry a request that times out?", paths)
    assert not leaks_location("How is your Docker image built and shipped?", paths)


def test_short_quote_stays_an_exact_excerpt():
    from viva.citations import normalize
    from viva.questions import short_quote

    long = "if (s_axis_tvalid) begin\n    s_axis_tready_reg <= !s_axis_tready_reg;\n    prescale_reg <= (prescale << 3)-1;\n    bit_cnt <= DATA_WIDTH+1;"
    q = short_quote(long)
    assert len(q) <= 120 and normalize(long).startswith(q)
    assert short_quote("return x + 1") == "return x + 1"
