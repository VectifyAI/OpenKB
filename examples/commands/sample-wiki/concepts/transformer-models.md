---
type: "Concept"
sources: ["summaries/attention-is-all-you-need.md"]
description: "Attention-only sequence models built around stacked self-attention."
---

# Transformer Models

Transformer models are neural sequence transduction models that replace recurrence and convolution with attention-only computation. They use stacked self-attention layers to model relationships between tokens, making them highly parallelizable and effective for tasks such as machine translation and parsing.

## Core idea

The Transformer architecture was introduced in [[summaries/attention-is-all-you-need]] as an alternative to recurrent sequence-to-sequence models. Instead of processing tokens sequentially, it computes interactions between positions through [[concepts/attention-mechanisms|attention mechanisms]], allowing all positions to be considered in parallel during training.

## Main architectural features

A standard Transformer uses an encoder-decoder structure:

- **Encoder**: a stack of identical layers, each containing
  - multi-head self-attention
  - a position-wise feed-forward network
  - residual connections and layer normalization
- **Decoder**: a similar stack with
  - masked self-attention to preserve autoregressive generation
  - encoder-decoder attention
  - a position-wise feed-forward network
  - residual connections and layer normalization

The model also uses [[concepts/positional-encoding|positional encoding]] to inject token order information, since the architecture itself has no recurrence or convolution.

## Why the architecture matters

Transformer models were important because they addressed several limitations of recurrent neural networks:

- **Parallelism**: training can process many positions at once
- **Shorter dependency paths**: long-range relationships are easier to learn
- **Efficiency**: they can train faster while achieving strong quality

The paper argues that self-attention reduces the maximum path length between positions to a constant per layer, which helps with long-distance dependencies.

## Attention in Transformers

Transformers use attention in three main places:

- **Encoder self-attention**: each input position attends to all other input positions
- **Decoder self-attention**: each output position attends only to earlier output positions
- **Encoder-decoder attention**: decoder states attend to encoder outputs

The paper uses **scaled dot-product attention** and **multi-head attention**. Multiple heads let the model attend to different subspaces and different kinds of relationships at the same time.

## Practical configuration from the paper

The original Transformer used:

- 6 encoder layers and 6 decoder layers
- model dimension 512 in the base model
- 8 attention heads
- feed-forward inner dimension 2048
- dropout and label smoothing during training

The paper also reported a larger model that achieved even better translation results.

## Results and impact

In the source paper, Transformer models achieved state-of-the-art performance on:

- [[entities/wmt-2014-english-german|WMT 2014 English-German]] translation
- [[entities/wmt-2014-english-french|WMT 2014 English-French]] translation

They also generalized well to English constituency parsing. The architecture became foundational for later advances in natural language processing and sequence modeling.

## Related concepts

- [[concepts/attention-mechanisms]]
- [[concepts/positional-encoding]]
- [[summaries/attention-is-all-you-need]]

## Notable significance

Transformer models are widely seen as a turning point in deep learning for language. Their attention-only design helped establish a new default architecture for machine translation, language modeling, and many other sequence tasks.