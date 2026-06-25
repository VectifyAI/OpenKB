---
name: transformer-attention
description: Use when reasoning about Transformer self-attention, multi-head attention, positional encoding, masked decoder attention, or why attention replaced recurrence/convolutions in sequence models; not for generic NLP or unrelated attention topics.
---

# Transformer Attention Reasoning

This skill encodes the practical worldview behind the original Transformer: sequence modeling works better when you stop stepping through tokens one at a time and instead let positions interact directly through attention. Use it to answer questions like “why does self-attention help,” “why do we need positional encoding,” “what does masking protect,” or “when is multi-head attention useful?”

## When to use this skill

- User is comparing Transformers against RNNs, LSTMs, GRUs, or convolutional seq2seq models
- User asks how self-attention, multi-head attention, or scaled dot-product attention works in the encoder or decoder
- User wants to know why positional encoding is required in an attention-only architecture
- User is debugging or explaining masked self-attention, autoregressive decoding, or encoder-decoder attention
- User asks why attention can shorten long-range dependency paths or improve parallelism
- Not for: generic “attention” in psychology, vision, or recommendation systems
- Not for: broad modern LLM training, prompting, or scaling-law questions unless the focus is the Transformer mechanism itself
- Not for: implementation-level optimization details unrelated to the architecture’s reasoning

## Core decision rules

- **When recurrence is the bottleneck, prefer attention-only computation** — recurrence forces sequential hidden-state updates and blocks parallelism within a training example.
- **When long-range dependencies matter, prefer self-attention over stacked recurrence or convolution** — any token can connect to any other token in one layer, so the path length stays short.
- **If the model has no recurrence or convolution, add explicit position information** — attention alone is permutation-blind, so positional encoding supplies order.
- **When decoding autoregressively, mask future positions** — otherwise the model leaks rightward information and can condition on tokens it should not know yet.
- **When one attention pattern seems too coarse, use multi-head attention** — separate heads let the model attend to different subspaces, positions, or relation types in parallel.
- **When dot products get too sharp at larger key dimensions, scale by \(\sqrt{d_k}\)** — this keeps softmax gradients usable and avoids overconfident attention scores.
- **If you need encoder-to-decoder alignment, use encoder-decoder attention, not plain self-attention** — the decoder should query the encoded source sequence directly.
- **When comparing layer types, evaluate sequential depth and maximum path length, not just parameter count** — the Transformer wins because it reduces sequential operations and dependency distance.
- **If the task is sentence-length sequence modeling, self-attention is often computationally attractive** — its per-layer complexity is favorable when sequence length is below representation width, which is common in translation.
- **When a single head seems to blur distinct relationships, interpret the averaging as a limitation, not a virtue** — multiple heads counteract that loss of resolution.
- **When output quality must remain stable, pair the architecture with residual connections, layer normalization, dropout, and label smoothing** — the paper treats these as part of making the attention stack train well.
- **If a learned positional embedding works, don’t assume it beats sinusoidal encoding** — the original result found similar performance; sinusoidal encodings were chosen for extrapolation potential.

## Approach

1. Identify which attention role is in play: encoder self-attention, masked decoder self-attention, or encoder-decoder attention.
2. Check whether the question is about ordering, dependency distance, or parallelization; those are the main reasons the architecture changes.
3. If the question concerns a design choice, test it against the paper’s core trade-off: sequential recurrence versus parallel attention with explicit position signals.
4. Use multi-head attention and scaling rules to explain expressiveness and training stability.
5. If the user is asking “why not RNNs?”, answer in terms of sequential computation, path length, and ease of long-range dependency learning.

## References

- [[references/original-transformer-paper]]

## Known gaps

- This skill is grounded in the original Transformer paper and does not cover later variants such as sparse attention, rotary position encodings, FlashAttention, or modern decoder-only LLM design.
- It does not provide implementation code, tensor shapes for every sublayer, or training-hyperparameter tuning advice beyond the architectural choices discussed in the source.
- It focuses on the reasoning for replacing recurrence; it does not deeply cover convolutional alternatives beyond their role as baselines in the comparison.
