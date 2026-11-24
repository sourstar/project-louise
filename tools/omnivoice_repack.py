#!/usr/bin/env python3
"""Repack omnivoice.cpp's two GGUF files into the two files llama.cpp loads.

  omnivoice-base-*.gguf       (arch "omnivoice-lm": tokenizer, text embeddings, 28 Qwen3 layers, audio tables)
  omnivoice-tokenizer-*.gguf  (arch "omnivoice-tokenizer": RVQ, DAC decoder and encoder, HuBERT, semantic encoder)
->
  <out>-lm.gguf      the text model (-m): arch "omnivoice", llama.cpp's tensor names, a tokenizer llama.cpp reads
  <out>-mmproj.gguf  the mmproj: audio embeddings, audio heads and the codec, under "a.gen."

No weight is touched: every tensor keeps its type and its bytes, only names and metadata change. Two things the
decoder graph needs are folded here, as omnivoice.cpp does when it loads the file:
  - a transposed convolution's weight [IC, OC, K] is laid out [OC * K, IC] (the same values, moved)
  - each snake activation gets its reciprocal beside it: inv_b = 1 / (alpha + 1e-9), in float32
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

import numpy as np


def lm_name(name: str) -> str | None:
    if name == "llm.embed_tokens.weight":
        return "token_embd.weight"
    if name == "llm.norm.weight":
        return "output_norm.weight"
    if not name.startswith("llm.layers."):
        return None
    _, _, bid, rest = name.split(".", 3)
    part = {
        "input_layernorm.weight":          "attn_norm.weight",
        "self_attn.q_proj.weight":         "attn_q.weight",
        "self_attn.k_proj.weight":         "attn_k.weight",
        "self_attn.v_proj.weight":         "attn_v.weight",
        "self_attn.o_proj.weight":         "attn_output.weight",
        "self_attn.q_norm.weight":         "attn_q_norm.weight",
        "self_attn.k_norm.weight":         "attn_k_norm.weight",
        "post_attention_layernorm.weight": "ffn_norm.weight",
        "mlp.gate_proj.weight":            "ffn_gate.weight",
        "mlp.up_proj.weight":              "ffn_up.weight",
        "mlp.down_proj.weight":            "ffn_down.weight",
    }[rest]
    return f"blk.{bid}.{part}"


class Derived:
    """A tensor made here, with the fields write() reads from a reader's tensor."""

    def __init__(self, data: np.ndarray, tensor_type):
        self.data = np.ascontiguousarray(data)
        self.tensor_type = tensor_type


# GGUF names hold 63 characters and omnivoice.cpp's go up to 71: the modules get short names in the mmproj
RENAMES = (
    ("acoustic_decoder.", "dec."),
    ("acoustic_encoder.", "enc."),
    ("encoder_semantic.", "sem."),
    ("semantic_model.encoder.layers.", "hubert.layers."),
    ("semantic_model.", "hubert."),
    ("quantizer.quantizers.", "rvq."),
    (".feed_forward.intermediate_dense.", ".ffn_up."),
    (".feed_forward.output_dense.", ".ffn_down."),
)


def mmproj_name(name: str) -> str:
    for old, new in RENAMES:
        name = name.replace(old, new)
    name = "a.gen." + name
    if len(name) > 63:
        raise SystemExit(f"{name}: name too long for GGUF")
    return name


def codec_tensors(gguf, tensors):
    for t in tensors:
        name = mmproj_name(t.name)
        if t.name.startswith("acoustic_decoder.") and t.name.endswith(".conv_t1.weight"):
            # numpy sees [IC, OC, K]; the graph multiplies by [IC, K * OC] in ggml's order, K the fast axis
            ic, oc, k = t.data.shape
            yield name, Derived(t.data.transpose(1, 2, 0).reshape(oc * k, ic), t.tensor_type)
        elif t.name.startswith(("acoustic_decoder.", "acoustic_encoder.")) and t.name.endswith(".alpha"):
            if t.tensor_type != gguf.GGMLQuantizationType.F32:
                raise SystemExit(f"{t.name}: expected float32")
            yield name, t
            inv_b = np.float32(1.0) / (t.data.astype(np.float32) + np.float32(1e-9))
            yield name[: -len(".alpha")] + ".inv_b", Derived(inv_b.astype(np.float32), t.tensor_type)
        else:
            yield name, t


def field(reader, key):
    f = reader.get_field(key)
    if f is None:
        raise SystemExit(f"{key} is missing")
    return f.contents()


def write(writer, tensors):
    for name, t in tensors:
        writer.add_tensor_info(name, t.data.shape, t.data.dtype, t.data.nbytes, t.tensor_type)
    writer.write_header_to_file()
    writer.write_kv_data_to_file()
    writer.write_ti_data_to_file()
    for _, t in tensors:
        writer.write_tensor_data(t.data)
    writer.close()


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--gguf-py", type=Path, help="llama.cpp's gguf-py directory, when gguf is not installed")
    ap.add_argument("base", type=Path)
    ap.add_argument("codec", type=Path)
    ap.add_argument("out", type=Path, help="output prefix: <out>-lm.gguf and <out>-mmproj.gguf")
    args = ap.parse_args()
    if args.gguf_py:
        sys.path.insert(0, str(args.gguf_py))
    import gguf

    base = gguf.GGUFReader(args.base)
    codec = gguf.GGUFReader(args.codec)
    if field(base, "general.architecture") != "omnivoice-lm" or field(codec, "general.architecture") != "omnivoice-tokenizer":
        raise SystemExit("expected omnivoice.cpp's base and tokenizer files, in this order")
    a = "omnivoice-lm"
    n_embd = field(base, f"{a}.embedding_length")
    file_type = {"Q8_0": gguf.LlamaFileType.MOSTLY_Q8_0, "F32": gguf.LlamaFileType.ALL_F32,
                 "F16": gguf.LlamaFileType.MOSTLY_F16, "BF16": gguf.LlamaFileType.MOSTLY_BF16,
                 "Q4_K_M": gguf.LlamaFileType.MOSTLY_Q4_K_M}.get(str(field(base, "general.file_type")))

    # the text model
    w = gguf.GGUFWriter(f"{args.out}-lm.gguf", "omnivoice")
    w.add_name("OmniVoice")
    if file_type is not None:
        w.add_file_type(file_type)
    w.add_quantization_version(gguf.GGML_QUANT_VERSION)
    w.add_block_count(field(base, f"{a}.block_count"))
    w.add_context_length(field(base, f"{a}.context_length"))
    w.add_embedding_length(n_embd)
    w.add_feed_forward_length(field(base, f"{a}.feed_forward_length"))
    w.add_head_count(field(base, f"{a}.attention.head_count"))
    w.add_head_count_kv(field(base, f"{a}.attention.head_count_kv"))
    w.add_key_length(field(base, f"{a}.attention.key_length"))
    w.add_value_length(field(base, f"{a}.attention.value_length"))
    w.add_layer_norm_rms_eps(field(base, f"{a}.attention.layer_norm_rms_epsilon"))
    w.add_rope_freq_base(field(base, f"{a}.rope.freq_base"))
    w.add_causal_attention(False)

    tokens = field(base, "tokenizer.ggml.tokens")
    # Qwen's vocabulary: the BPE tokens, then the added ones; "<|...|>" are control tokens, the rest are matched as text
    n_bpe = tokens.index("<|endoftext|>")
    types = [gguf.TokenType.NORMAL] * n_bpe + [
        gguf.TokenType.CONTROL if t.startswith("<|") and t.endswith("|>") else gguf.TokenType.USER_DEFINED
        for t in tokens[n_bpe:]
    ]
    w.add_tokenizer_model("gpt2")
    w.add_tokenizer_pre("qwen2")
    w.add_token_list(tokens)
    w.add_token_types(types)
    w.add_token_merges(field(base, "tokenizer.ggml.merges"))
    w.add_eos_token_id(tokens.index("<|im_end|>"))
    w.add_pad_token_id(n_bpe)
    w.add_add_bos_token(False)

    lm = [(lm_name(t.name), t) for t in base.tensors if lm_name(t.name)]
    if len(lm) != field(base, f"{a}.block_count") * 11 + 2:
        raise SystemExit(f"unexpected tensors in the base file: {len(lm)} of the text model")
    write(w, lm)

    # the mmproj
    w = gguf.GGUFWriter(f"{args.out}-mmproj.gguf", "clip")
    w.add_name("OmniVoice audio")
    w.add_type(gguf.GGUFType.MMPROJ)
    if file_type is not None:
        w.add_file_type(file_type)
    w.add_quantization_version(gguf.GGML_QUANT_VERSION)
    w.add_clip_has_gen_audio_encoder(True)
    w.add_clip_gen_audio_projector_type("omnivoice")
    w.add_gen_audio_projection_dim(n_embd)
    # libmtmd reads these for every model; the codec's own sizes are fixed in the code
    w.add_gen_audio_embedding_length(n_embd)
    w.add_gen_audio_feed_forward_length(field(base, f"{a}.feed_forward_length"))
    w.add_gen_audio_block_count(field(base, f"{a}.block_count"))
    w.add_gen_audio_head_count(field(base, f"{a}.attention.head_count"))
    w.add_gen_audio_attention_layernorm_eps(field(base, f"{a}.attention.layer_norm_rms_epsilon"))

    mm = [(mmproj_name(t.name), t) for t in base.tensors if not lm_name(t.name)]
    if sorted(n for n, _ in mm) != ["a.gen.audio_embeddings.weight", "a.gen.audio_heads.weight"]:
        raise SystemExit("unexpected tensors in the base file")
    mm += list(codec_tensors(gguf, codec.tensors))
    write(w, mm)

    print(f"{args.out}-lm.gguf: {len(lm)} tensors; {args.out}-mmproj.gguf: {len(mm)} tensors")


if __name__ == "__main__":
    main()
