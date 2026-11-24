"""tools/omnivoice_repack.py: omnivoice.cpp's two model files renamed into the
text model and the mmproj llama.cpp loads (docs/notes/omnivoice-on-our-backend.md §7.3)."""
import numpy as np
import pytest

from tools import omnivoice_repack as rp


def test_the_backbone_takes_llama_cpps_tensor_names():
    assert rp.lm_name("llm.embed_tokens.weight") == "token_embd.weight"
    assert rp.lm_name("llm.norm.weight") == "output_norm.weight"
    assert rp.lm_name("llm.layers.27.self_attn.q_norm.weight") == "blk.27.attn_q_norm.weight"
    assert rp.lm_name("llm.layers.0.mlp.gate_proj.weight") == "blk.0.ffn_gate.weight"
    assert rp.lm_name("llm.layers.3.post_attention_layernorm.weight") == "blk.3.ffn_norm.weight"


def test_what_is_not_the_backbone_goes_to_the_mmproj():
    assert rp.lm_name("audio_embeddings.weight") is None and rp.lm_name("audio_heads.weight") is None
    assert rp.mmproj_name("audio_heads.weight") == "a.gen.audio_heads.weight"
    assert rp.mmproj_name("quantizer.quantizers.7.project_out.bias") == "a.gen.rvq.7.project_out.bias"
    assert rp.mmproj_name("acoustic_decoder.block.4.res_unit3.snake2.alpha") == "a.gen.dec.block.4.res_unit3.snake2.alpha"


def test_omnivoice_cpps_longest_name_fits_gguf_and_an_unknown_long_one_is_refused():
    longest = "semantic_model.encoder.layers.11.feed_forward.intermediate_dense.weight"
    assert len(longest) == 71
    assert rp.mmproj_name(longest) == "a.gen.hubert.layers.11.ffn_up.weight"
    with pytest.raises(SystemExit):
        rp.mmproj_name("some_module." + "x" * 60)


class _Tensor:
    def __init__(self, name, data, tensor_type):
        self.name, self.data, self.tensor_type = name, data, tensor_type


class _Gguf:
    class GGMLQuantizationType:
        F32, F16 = "F32", "F16"


def test_a_transposed_convolution_is_laid_out_for_the_multiplication_and_a_snake_gets_its_reciprocal():
    ic, oc, k = 3, 2, 4
    w = np.arange(ic * oc * k, dtype=np.float16).reshape(ic, oc, k)
    alpha = np.asarray([[0.5], [2.0], [1e-12]], dtype=np.float32)
    got = dict(rp.codec_tensors(_Gguf, [
        _Tensor("acoustic_decoder.block.0.conv_t1.weight", w, "F16"),
        _Tensor("acoustic_decoder.block.0.snake1.alpha", alpha, "F32"),
        _Tensor("fc2.bias", np.zeros(4, dtype=np.float32), "F32"),
    ]))
    packed = got["a.gen.dec.block.0.conv_t1.weight"].data
    assert packed.shape == (oc * k, ic) and packed.dtype == np.float16
    for i in range(ic):
        for o in range(oc):
            for j in range(k):
                assert packed[o * k + j, i] == w[i, o, j], "the same values, moved"
    inv_b = got["a.gen.dec.block.0.snake1.inv_b"].data
    assert inv_b.dtype == np.float32 and inv_b.shape == alpha.shape
    assert inv_b[0, 0] == np.float32(2.0) and inv_b[1, 0] == np.float32(0.5)
    assert inv_b[2, 0] == np.float32(1.0) / (np.float32(1e-12) + np.float32(1e-9)), "float32 arithmetic, as the C code"
    assert got["a.gen.dec.block.0.snake1.alpha"].data is alpha and got["a.gen.fc2.bias"].tensor_type == "F32"


def test_a_snake_that_is_not_float32_is_refused():
    with pytest.raises(SystemExit):
        list(rp.codec_tensors(_Gguf, [_Tensor("acoustic_encoder.block.0.snake1.alpha", np.zeros((2, 1), np.float16), "F16")]))
