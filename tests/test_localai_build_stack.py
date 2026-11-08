"""deploy/localai/llama-cpp: the AOT and pinned-Intel-stack switches of build.sh
(rebuild steps a+b, operator 2026-11-07). The build itself needs docker and the
network; what is checked here is what decides WHICH engine comes out."""
from __future__ import annotations

import re
import subprocess
from pathlib import Path

HERE = Path(__file__).resolve().parents[1] / "deploy" / "localai" / "llama-cpp"
BUILD = HERE / "build.sh"


def _stack_lines(path: Path) -> list[tuple[str, str]]:
    out = subprocess.run(["sh", "-c", f'. "{path}"; printf "%s\\n" "$DEBS"'],
                         capture_output=True, text=True, check=True).stdout
    return [tuple(line.split()) for line in out.splitlines() if line.strip()]


def test_every_stack_file_pins_its_image_and_debs():
    files = sorted(HERE.glob("intel-stack-*.conf"))
    assert files
    for f in files:
        text = f.read_text(encoding="utf-8")
        image = re.search(r"^ONEAPI_IMAGE=(\S+)$", text, re.M)
        prebuilt = re.search(r"^STACK_ON=prebuilt$", text, re.M)
        # either its own oneAPI image, pinned, or LocalAI's prebuilt builder (whose digest the lock records)
        assert bool(image) != bool(prebuilt), f
        if image:
            assert ":" in image.group(1) and "latest" not in image.group(1), f
        lines = _stack_lines(f)
        assert lines, f
        for want, url in lines:
            assert url.startswith("https://") and url.endswith(".deb"), (f, url)
            assert want == "-" or re.fullmatch(r"[0-9a-f]{64}", want), (f, want)
        # the driver Level Zero talks to and the compiler for the card are pinned, the driver by its sum
        names = {Path(u).name: w for w, u in lines}
        assert any(n.startswith("libze-intel-gpu1_") and w != "-" for n, w in names.items()), f
        assert any(n.startswith("intel-igc-core-2_") for n in names), f


def test_unset_switches_keep_the_live_tag():
    text = BUILD.read_text(encoding="utf-8")
    # the default name is the series' own, as before; each switch only appends
    assert "NAME=$VARIANT-$HEAD" in text
    assert "NAME=$NAME-aot-$SYCL_DEVICE_ARCH" in text
    assert "NAME=$NAME-stack-$STACK" in text
    assert '${CMAKE_ARGS:+--build-arg CMAKE_ARGS="$CMAKE_ARGS"}' in text
    assert "-DGGML_SYCL_DEVICE_ARCH=$SYCL_DEVICE_ARCH" in text


def test_a_sum_mismatch_stops_the_build():
    text = BUILD.read_text(encoding="utf-8")
    assert 'sha256 mismatch' in text and "exit 3" in text
    assert "set -eu" in text


def test_the_stack_pins_every_deb_by_its_sum():
    # the first a+b build recorded the IGC and Level Zero sums; the stack now pins them (no "-")
    for f in sorted(HERE.glob("intel-stack-*.conf")):
        assert all(want != "-" for want, _ in _stack_lines(f)), f


def test_one_igc_the_pinned_one_compiles_and_ships():
    # 2026-11-07: the PPA's libigc 2.41.16 beside the pinned 2.41.5 was both packaged and (AOT) used by ocloc
    text = (HERE / "Dockerfile.intel-stack").read_text(encoding="utf-8")
    for name in ("libigc.so*", "libiga64.so*", "libigdfcl.so*"):
        assert name in text
    assert "apt-get remove -y $owners" in text and "ldconfig" in text
    assert "another IGC remains" in text
    assert "ocloc compile -file k.cl -device bmg-g31" in text
    assert "grep -q '/usr/local/lib/libigc.so' /intel-stack.ocloc" in text
    assert 'ENV INTEL_DRIVER_LIB_DIRS="/usr/local/lib ' in text
    build = BUILD.read_text(encoding="utf-8")
    assert "oneapi_image_digest" in build and "ocloc_igc" in build
    # BuildKit keeps a pulled base out of docker's image store: the digest comes from the registry
    assert "docker buildx imagetools inspect" in build
    # a changed layer is another engine (the IGC fix first rebuilt under the old tag)
    assert 'stack_layer $(sha256sum "$HERE/Dockerfile.intel-stack"' in build
    # and its device code is compiled, never taken from another build's ccache
    assert "ENV CCACHE_DISABLE=1" in text


def test_an_aot_build_records_the_ocloc_and_igc_its_driver_used():
    # the real path, not the layer's plain environment: oneAPI's image carries other ocloc/libigc copies (GTPin)
    proof = (HERE / "aot-proof.sh").read_text(encoding="utf-8")
    assert "source /opt/intel/oneapi/setvars.sh" in proof
    assert "-fsycl-targets=spir64_gen" in proof and "strace -f -e trace=execve" in proof
    assert "LD_DEBUG=libs" in proof
    assert 'echo "aot_ocloc ' in proof and 'echo "aot_igc ' in proof
    build = BUILD.read_text(encoding="utf-8")
    assert '/aot-proof.sh "$SYCL_DEVICE_ARCH"' in build


def test_p7_is_upstreams_dmmv_cleanup_and_in_the_series():
    patches = HERE.parent / "patches"
    series = (patches / "series").read_text(encoding="utf-8").split()
    assert "0007-llama-cpp-sycl-dmmv-cleanup.patch" in series
    p7 = (patches / "0007-llama-cpp-sycl-dmmv-cleanup.patch").read_text(encoding="utf-8")
    # it adds the llama.cpp patch beside the others, and that patch is upstream's commit, dmmv.cpp only
    assert "backend/cpp/llama-cpp/patches/0009-sycl-dmmv-cleanup.patch" in p7
    assert "5e18eb021b021a133cfa4d9ac84dab644bc24318" in p7
    inner = [l for l in p7.splitlines() if l.startswith("+diff --git ")]
    assert inner == ["+diff --git a/ggml/src/ggml-sycl/dmmv.cpp b/ggml/src/ggml-sycl/dmmv.cpp"]


def test_a_prebuilt_stack_keeps_the_builders_loader_and_says_which_ships():
    # 2026-11-08: Level Zero loaders >= 1.32 refuse on the node and oneAPI 2026.2's libsycl 9 segfaults, so a
    # prebuilt stack layers only runtime debs on LocalAI's own builder and never pins the loader
    build = BUILD.read_text(encoding="utf-8")
    assert "STACK_ON=image" in build and "stack_on prebuilt $BUILDER" in build
    assert 'STACK_BASE=$BUILDER' in build and '--build-arg BUILDER_BASE="$STACK_BASE"' in build
    assert "builder_image_digest $STACK_BASE" in build and "oneapi_image_digest $ONEAPI_IMAGE" in build
    assert "ze_loader $(docker run" in build
    # an unknown mode stops the build
    assert "STACK_ON in $INTEL_STACK: image | prebuilt" in build
    for f in sorted(HERE.glob("intel-stack-*.conf")):
        if re.search(r"^STACK_ON=prebuilt$", f.read_text(encoding="utf-8"), re.M):
            names = [Path(u).name for _, u in _stack_lines(f)]
            assert not any(n.startswith(("libze1_", "libze-dev_")) for n in names), f
    # the versions file lists a package the builder does not have instead of failing the layer
    stack = (HERE / "Dockerfile.intel-stack").read_text(encoding="utf-8")
    assert 'echo "$p not-installed"' in stack


def test_the_switches_are_refused_outside_sycl():
    r = subprocess.run(["sh", str(BUILD), "cpu", "/nonexistent"], capture_output=True, text=True,
                       env={"SYCL_DEVICE_ARCH": "bmg-g31", "PATH": "/usr/bin:/bin"})
    # refused before anything is fetched or built
    assert r.returncode == 2 and "sycl_f16" in r.stderr
