"""tools/measure/encoder_precision.py: the arms built and read back, a refused
arm reported, every arm compared with the stored vectors and the reference
arm, the sample read as cross_device_check exports it (no OpenVINO or torch
needed: the arms are fakes)."""
import json
import math

import pytest

from tools.measure import encoder_precision as ep


def test_compare_reads_unit_vectors_and_counts_identical_rows():
    stored = [[3.0, 4.0], [1.0, 0.0]]
    same = ep.compare([[3.0, 4.0], [1.0, 0.0]], stored)
    assert same["min_cosine"] == pytest.approx(1.0) and same["identical_rows"] == 2
    assert same["max_abs_diff_unit"] == 0.0
    # a raw row against a normalised one: the same direction, no difference
    scaled = ep.compare([[0.6, 0.8], [2.0, 0.0]], stored)
    assert scaled["max_abs_diff_unit"] == pytest.approx(0.0, abs=1e-12) and scaled["identical_rows"] == 0
    off = ep.compare([[3.0, 4.0], [1.0, 0.1]], stored)
    assert off["min_cosine"] == pytest.approx(1 / math.sqrt(1.01))
    with pytest.raises(ValueError):
        ep.compare([[1.0]], stored)


def test_the_sample_is_read_as_the_cross_device_export_writes_it(tmp_path):
    path = tmp_path / "s.json"
    path.write_text(json.dumps([{"chunk": "a", "input": ["title", "abstract"], "stored": [1.0, 0.0]},
                                {"chunk": "b", "input": "one text", "stored": [0.0, 1.0]}]), encoding="utf-8")
    inputs, stored = ep.read_sample(path)
    assert inputs == [("title", "abstract"), "one text"] and stored == [[1.0, 0.0], [0.0, 1.0]]
    path.write_text("[]", encoding="utf-8")
    with pytest.raises(SystemExit):
        ep.read_sample(path)


def test_every_arm_is_read_back_and_compared_and_a_refused_one_is_said():
    inputs, stored = ["x", "y"], [[1.0, 0.0], [0.0, 1.0]]

    def arm(precision, vectors):
        return lambda: ({"INFERENCE_PRECISION_HINT": precision}, lambda item: vectors[inputs.index(item)])

    def refused():
        raise RuntimeError("no GPU device")
    arms = ep.run_arms([("gpu-live", refused),
                        ("cpu-default", arm("bf16", [[1.0, 0.01], [0.0, 1.0]])),
                        ("cpu-f32", arm("f32", [[1.0, 0.0], [0.0, 1.0]]))], inputs)
    result = ep.report(arms, stored, "cpu-f32", {"device_defaults": {}})
    rows = {r["arm"]: r for r in result["arms"]}
    assert rows["gpu-live"]["refused"] == "RuntimeError: no GPU device"
    assert rows["cpu-default"]["read_back"] == {"INFERENCE_PRECISION_HINT": "bf16"}
    assert rows["cpu-default"]["against_stored"]["identical_rows"] == 1
    assert rows["cpu-default"]["against_cpu-f32"]["min_cosine"] < 1.0
    assert rows["cpu-f32"]["against_stored"]["identical_rows"] == 2 and "against_cpu-f32" not in rows["cpu-f32"]
    assert result["reference_arm"] == "cpu-f32" and result["device_defaults"] == {}


def test_without_its_reference_arm_the_report_says_so():
    arms = ep.run_arms([("cpu-f32", lambda: (_ for _ in ()).throw(RuntimeError("refused")))], ["x"])
    result = ep.report(arms, [[1.0]], "cpu-f32")
    assert result["reference_arm"] is None and result["arms"][0]["refused"] == "RuntimeError: refused"


def test_the_arms_are_the_live_compile_f32_and_the_cpu_twins():
    assert [a[0] for a in ep.OV_ARMS] == ["gpu-live", "gpu-f32", "cpu-default", "cpu-f32"]
    live = dict((n, (d, c)) for n, d, c in ep.OV_ARMS)
    assert live["gpu-live"] == ("GPU", {})                     # as deploy/medcpt-rerank/server.py compiles it
    assert live["gpu-f32"] == ("GPU", {"INFERENCE_PRECISION_HINT": "f32"})
    assert ep.OV_PROPERTIES == ("INFERENCE_PRECISION_HINT", "EXECUTION_MODE_HINT")


def test_dar_legal_it_is_not_measured_here():
    import inspect
    source = inspect.getsource(ep)
    assert "dar_legal_it" not in source and "torch_arms" not in source
    assert "Don't fucking touch dar legal" in source
