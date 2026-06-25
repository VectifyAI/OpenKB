---
type: "Concept"
sources: ["summaries/attention-is-all-you-need.md"]
description: "Position information added to attention-only models so they can use order."
---

# Positional Encoding

## Overview

Positional encoding is the mechanism used in the Transformer to inject order information into token embeddings when the model has no recurrence or convolution. In [[summaries/attention-is-all-you-need]], it is the solution that lets the attention-only architecture know where each token appears in a sequence.

## Why it is needed

The Transformer model removes the sequential structure of recurrent networks. That makes it highly parallelizable, but it also means the model has no built-in sense of token order. Without an explicit position signal, the same set of tokens would look identical regardless of arrangement.

Positional encoding fixes this by adding a position-dependent vector to each input embedding at the bottom of the encoder and decoder stacks.

## How it works

The paper uses fixed sinusoidal encodings:

- even dimensions use sine functions
- odd dimensions use cosine functions
- each dimension has a different frequency

This creates a position vector with the same dimensionality as the embeddings, so the two can be summed directly.

The encoding is defined so that relative offsets can be represented as linear combinations of the encodings, which helps the model learn to attend by relative position.

## Key properties

### Same dimensionality as embeddings
The positional vectors have dimension equal to the model dimension, so they can be added to token embeddings without changing shape.

### Fixed, not learned
The paper experimented with learned positional embeddings and found similar results, but chose sinusoidal encodings because they may generalize better to sequence lengths not seen during training.

### Supports extrapolation
Because the encoding is deterministic and not tied to a fixed lookup table, it can in principle be applied to longer sequences than those encountered during training.

## Role in the Transformer

Positional encoding is part of the input representation for both the encoder and decoder. It works alongside [[concepts/attention-mechanisms]] and [[concepts/transformer-models]] to make attention-based sequence modeling possible without recurrence.

## Summary

Positional encoding gives the Transformer access to token order while preserving the model’s fully parallel structure. In the original paper, sinusoidal positional encodings were an effective and elegant choice that performed on par with learned embeddings and offered potential extrapolation benefits.
