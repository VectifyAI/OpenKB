---
sources: ["summaries/attention-is-all-you-need.md"]
type: "Product"
description: "Open-source TensorFlow toolkit used to implement and evaluate the Transformer"
---

## Overview
Tensor2Tensor is a TensorFlow-based research toolkit for building and evaluating sequence modeling models. In [[summaries/attention-is-all-you-need]], it is described as the codebase that the authors used to develop, train, and evaluate the Transformer.

## Role in the paper
The paper credits Tensor2Tensor as an important part of the implementation effort behind the Transformer work. It was used to replace an earlier codebase and to accelerate experimentation, model tuning, and evaluation.

## Key facts from the document
- Used for designing, implementing, tuning, and evaluating Transformer variants.
- Helped replace an earlier internal codebase.
- Supported the authors' translation experiments and broader model development.
- Mentioned in the paper's closing note as the public codebase associated with the work.

## Relationship to the Transformer
Tensor2Tensor is closely associated with the development of [[concepts/transformer-models|Transformer models]] described in [[summaries/attention-is-all-you-need]]. The toolkit provided the experimental infrastructure for the architecture that relies on [[concepts/attention-mechanisms|attention mechanisms]] and [[concepts/positional-encoding|positional encoding]].

## Related entities
- [[entities/google]]
- [[entities/google-brain]]
- [[entities/google-research]]
- [[entities/wmt-2014-english-german]]
- [[entities/wmt-2014-english-french]]