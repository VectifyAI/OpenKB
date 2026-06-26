---
type: "Summary"
description: "Introduces the Transformer, a fully attention-based sequence model for translation."
doc_type: short
full_text: "sources/attention-is-all-you-need.md"
---

# Attention Is All You Need

## Summary

This paper introduces the [[concepts/transformer-models|Transformer]], a new sequence transduction architecture that replaces recurrent and convolutional layers with attention-only computation. The main claim is that self-attention is sufficient to model dependencies in encoder-decoder tasks while enabling much greater parallelization and faster training.

## Core idea

Traditional sequence-to-sequence systems relied on recurrent neural networks or convolutions, often combined with attention mechanisms. The Transformer removes recurrence entirely and uses stacked [[concepts/attention-mechanisms|attention mechanisms]] in both the encoder and decoder, plus position-wise feed-forward networks.

This design has three major benefits:

- higher training parallelism
- shorter paths for long-range dependencies
- strong translation quality with less compute

## Architecture

The Transformer uses an encoder-decoder structure:

- **Encoder**: 6 identical layers, each with
  - multi-head self-attention
  - position-wise feed-forward network
  - residual connections and layer normalization
- **Decoder**: 6 identical layers, each with
  - masked self-attention
  - encoder-decoder attention
  - position-wise feed-forward network
  - residual connections and layer normalization

The model uses:

- scaled dot-product attention
- [[concepts/attention-mechanisms|multi-head attention]]
- learned token embeddings
- sinusoidal [[concepts/positional-encoding|positional encoding]]

## Attention mechanism

Attention maps a query and a set of key-value pairs to an output vector computed as a weighted sum of values. The paper defines:

- **Scaled dot-product attention**: attention scores are scaled by the square root of key dimension to avoid large dot products.
- **Multi-head attention**: multiple learned projections allow the model to attend to different representation subspaces and positions simultaneously.

The authors argue that multi-head attention improves expressiveness compared with a single attention head.

## Why self-attention

The paper compares self-attention with recurrent and convolutional layers on:

- per-layer computational complexity
- amount of parallelizable computation
- maximum path length between positions

Main conclusion:

- self-attention gives constant sequential depth
- it shortens paths between distant tokens
- it can be more efficient than recurrence for typical sentence-length inputs

A table in the paper shows that self-attention has constant sequential operations and constant maximum path length per layer, while recurrent and convolutional alternatives require longer sequential computation or longer dependency paths.

## Positional information

Because the architecture has no recurrence or convolution, it must add position information explicitly. The paper uses fixed sinusoidal encodings, chosen because they may generalize to longer sequences and make relative position reasoning easier. Learned positional embeddings were also tested and gave similar results.

## Training setup

The models were trained on [[entities/wmt-2014|WMT 2014]] [[entities/wmt-2014-english-german|English-German]] and [[entities/wmt-2014-english-french|English-French]] translation data using:

- byte-pair encoding or word-piece vocabularies
- Adam optimizer
- learning rate warmup and inverse square-root decay
- dropout and label smoothing

The big model trained on 8 P100 GPUs for about 3.5 days; the base model trained for about 12 hours.

## Results

The Transformer achieved state-of-the-art results at the time:

- **[[entities/wmt-2014-english-german|WMT 2014 English-German]]**: 28.4 BLEU with the big model
- **[[entities/wmt-2014-english-french|WMT 2014 English-French]]**: 41.8 BLEU with the big model

These results surpassed previous systems, including ensembles, while using substantially less training cost.

## Ablations and variations

The paper evaluates architectural variations and finds that:

- more attention heads help up to a point
- too small key dimensions hurt performance
- larger models perform better
- dropout is important for generalization
- learned positional embeddings perform similarly to sinusoidal ones

## Generalization beyond translation

To test whether the model transfers beyond machine translation, the authors apply it to English constituency parsing. The Transformer performs competitively on both supervised and semi-supervised settings, showing that the architecture generalizes well to structured prediction tasks.

## Interpretation and visualization

Attention visualizations suggest that individual heads learn different behaviors, such as:

- long-distance dependency tracking
- anaphora resolution
- syntactic structure recognition

The authors present these as evidence that attention heads can capture interpretable linguistic patterns.

## Conclusion

The paper establishes the Transformer as a simple and effective alternative to recurrent and convolutional sequence models. Its attention-only design improves parallelism, reduces training cost, and achieves state-of-the-art translation quality. The work became foundational for modern [[concepts/transformer-models|Transformer models]] in natural language processing and beyond.

## Related Concepts
- [[concepts/attention-mechanisms]]
- [[concepts/transformer-models]]
- [[concepts/positional-encoding]]

## Entities
- [[entities/google]]
- [[entities/google-brain]]
- [[entities/google-research]]
- [[entities/university-of-toronto]]
- [[entities/nips-2017]]
- [[entities/wmt-2014]]
- [[entities/wmt-2014-english-german]]
- [[entities/wmt-2014-english-french]]
- [[entities/tensor2tensor]]
