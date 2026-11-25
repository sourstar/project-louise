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
  - each codebook gets the squared norm of its entries (the quantizer's score is 2 e.z - |e|^2)
  - a convolution kernel stored quantized becomes float16
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


# the "tts" chat template of llama.cpp: the prompt of a text to speak. A voice asked by name is a description of it
# ("female, young adult"), which the pipeline reads between the two markers
TTS_TEMPLATE = (
    "{%- if voice is defined and voice -%}<|instruct_start|>{{ voice }}<|instruct_end|>{%- endif -%}"
    "{{ messages[0]['content'] }}"
)


class Derived:
    """A tensor made here, with the fields write() reads from a reader's tensor."""

    def __init__(self, data: np.ndarray, tensor_type):
        self.data = np.ascontiguousarray(data)
        self.tensor_type = tensor_type


# GGUF names hold 63 characters and omnivoice.cpp's go up to 71: the modules get short names in the mmproj.
# "a.gen." is what speech generation loads, "a.spk." what reads a voice clip (the audio encoder of the mmproj)
RENAMES = (
    ("acoustic_decoder.", "a.gen.dec."),
    ("fc2.", "a.gen.fc2."),
    ("audio_embeddings.", "a.gen.audio_embeddings."),
    ("audio_heads.", "a.gen.audio_heads."),
    ("acoustic_encoder.", "a.spk.enc."),
    ("encoder_semantic.", "a.spk.sem."),
    ("semantic_model.encoder.layers.", "a.spk.hubert.layers."),
    ("semantic_model.", "a.spk.hubert."),
    ("fc.", "a.spk.fc."),
)
SHORTER = (
    (".feed_forward.intermediate_dense.", ".ffn_up."),
    (".feed_forward.output_dense.", ".ffn_down."),
)
QUANTIZER = "quantizer.quantizers."


def mmproj_name(name: str) -> str:
    if name.startswith(QUANTIZER):
        # the codebooks and their way out decode; the way in only encodes a clip
        rest = name[len(QUANTIZER):]
        out = ("a.spk.rvq." if ".project_in." in rest else "a.gen.rvq.") + rest
    else:
        for old, new in RENAMES:
            if name.startswith(old):
                out = new + name[len(old):]
                break
        else:
            raise SystemExit(f"{name}: not a tensor of omnivoice.cpp's files")
    for old, new in SHORTER:
        out = out.replace(old, new)
    if len(out) > 63:
        raise SystemExit(f"{out}: name too long for GGUF")
    return out


def codec_tensors(gguf, tensors):
    f32 = gguf.GGMLQuantizationType.F32
    for t in tensors:
        name = mmproj_name(t.name)
        if t.name.startswith("acoustic_decoder.") and t.name.endswith(".conv_t1.weight"):
            # numpy sees [IC, OC, K]; the graph multiplies by [IC, K * OC] in ggml's order, K the fast axis
            ic, oc, k = t.data.shape
            yield name, Derived(t.data.transpose(1, 2, 0).reshape(oc * k, ic), t.tensor_type)
        elif t.name.startswith(("acoustic_decoder.", "acoustic_encoder.")) and t.name.endswith(".alpha"):
            if t.tensor_type != f32:
                raise SystemExit(f"{t.name}: expected float32")
            yield name, t
            inv_b = np.float32(1.0) / (t.data.astype(np.float32) + np.float32(1e-9))
            yield name[: -len(".alpha")] + ".inv_b", Derived(inv_b.astype(np.float32), t.tensor_type)
        elif t.name.startswith(QUANTIZER) and t.name.endswith(".codebook.embed"):
            if t.tensor_type != f32:
                raise SystemExit(f"{t.name}: expected float32")
            yield name, t
            # |e|^2 of each entry, summed in float32 in the order omnivoice.cpp sums it
            sq = np.zeros(t.data.shape[0], dtype=np.float32)
            for i in range(t.data.shape[1]):
                sq += t.data[:, i] * t.data[:, i]
            yield name.replace("a.gen.rvq.", "a.spk.rvq.")[: -len(".codebook.embed")] + ".embed_sq", Derived(sq, f32)
        elif len(t.shape) == 3 and t.tensor_type not in (f32, gguf.GGMLQuantizationType.F16):
            # a quantized convolution kernel (HuBERT's positional conv): the convolution reads float16
            shape = tuple(int(n) for n in reversed(t.shape))
            w = gguf.quants.dequantize(t.data, t.tensor_type).reshape(shape)
            yield name, Derived(w.astype(np.float16), gguf.GGMLQuantizationType.F16)
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
    w.add_chat_template([{"name": "tts", "template": TTS_TEMPLATE}])

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
    # the audio encoder of the mmproj reads a voice clip: one backbone row per codec frame
    w.add_clip_has_audio_encoder(True)
    w.add_clip_audio_projector_type("omnivoice_spkenc")
    w.add_audio_projection_dim(n_embd)
    w.add_audio_embedding_length(field(codec, "omnivoice.semantic.hidden_size"))
    w.add_audio_feed_forward_length(field(codec, "omnivoice.semantic.intermediate_size"))
    w.add_audio_block_count(field(codec, "omnivoice.semantic.num_hidden_layers"))
    w.add_audio_head_count(field(codec, "omnivoice.semantic.num_attention_heads"))
    w.add_audio_attention_layernorm_eps(field(codec, "omnivoice.semantic.layer_norm_eps"))
    w.add_audio_num_mel_bins(1)  # the raw waveform, one channel in the place of the mel bins

    mm = [(mmproj_name(t.name), t) for t in base.tensors if not lm_name(t.name)]
    if sorted(n for n, _ in mm) != ["a.gen.audio_embeddings.weight", "a.gen.audio_heads.weight"]:
        raise SystemExit("unexpected tensors in the base file")
    mm += list(codec_tensors(gguf, codec.tensors))
    write(w, mm)

    print(f"{args.out}-lm.gguf: {len(lm)} tensors; {args.out}-mmproj.gguf: {len(mm)} tensors")


if __name__ == "__main__":
    main()
