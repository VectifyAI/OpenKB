---
type: "Concept"
sources: ["summaries/attention-is-all-you-need.md"]
description: "Methods that weight sequence elements to focus computation on relevant inputs."
---

# Attention Mechanisms

## Overview

Attention mechanisms are techniques for computing a weighted combination of values based on the relevance of a query to a set of keys. In sequence models, they let a model focus on the most relevant parts of an input or output sequence when producing a representation or a prediction.

In [[summaries/attention-is-all-you-need]], attention is the central building block of the [[concepts/transformer-models|Transformer]], replacing recurrence and convolution entirely.

## Core idea

An attention mechanism takes:

- a **query**
- a set of **keys**
- a set of **values**

It produces an output by scoring how well the query matches each key, turning those scores into weights, and returning a weighted sum of the values.

This makes attention useful for:

- aligning input and output tokens
- modeling long-range dependencies
- selecting relevant context dynamically

## Attention in the Transformer

The paper describes two important forms of attention:

### Scaled dot-product attention

The Transformer computes attention using dot products between queries and keys, scaled by the square root of the key dimension before applying softmax. This scaling helps prevent large dot products from producing overly sharp softmax distributions.

### Multi-head attention

Instead of using a single attention operation, the model projects queries, keys, and values into multiple subspaces and applies attention in parallel. The outputs are concatenated and projected again.

This lets the model attend to different kinds of relationships at the same time, such as syntax, position, or semantic association.

## Self-attention

A major special case is [[concepts/attention-mechanisms|self-attention]], where queries, keys, and values all come from the same sequence. In the Transformer:

- the encoder uses self-attention to let each position attend to all others in the input
- the decoder uses masked self-attention so each position can only attend to earlier outputs
- encoder-decoder attention lets the decoder attend to the encoded input sequence

Self-attention is the key mechanism that allows the Transformer to avoid recurrence while still modeling dependencies across the full sequence.

## Why it matters

The paper argues that attention mechanisms are advantageous because they:

- reduce the number of sequential operations
- improve parallelization during training
- shorten paths between distant tokens
- work well for translation and parsing tasks

Compared with recurrent layers, attention can connect any pair of positions in constant depth. Compared with convolutions, it avoids needing many stacked layers to relate distant positions.

## Positional information

Because attention alone does not encode token order, the Transformer adds [[concepts/positional-encoding|positional encoding]] to token embeddings. This gives the model information about sequence position while preserving the parallel structure of attention-based computation.

## Key takeaways from the paper

- Attention is a flexible mechanism for dynamic relevance weighting.
- Self-attention can replace recurrence in sequence models.
- Multi-head attention improves expressiveness by attending from multiple subspaces.
- Attention-only architectures can achieve strong results on machine translation and parsing.

## Related pages

- [[concepts/transformer-models]]
- [[concepts/positional-encoding]]
- [[summaries/attention-is-all-you-need]]