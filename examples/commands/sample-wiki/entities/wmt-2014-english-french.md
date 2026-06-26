---
sources: ["summaries/attention-is-all-you-need.md"]
type: "Event"
description: "The WMT 2014 English-French machine translation benchmark."
---

## Overview
WMT 2014 English-French is a machine translation evaluation task and dataset used in the paper [[summaries/attention-is-all-you-need]]. It serves as one of the main benchmarks for comparing translation quality and training cost across neural machine translation systems.

## Role in the paper
The paper reports results on this benchmark to show that the [[concepts/transformer-models|Transformer]] achieves strong translation quality with much lower training cost than prior models.

## Key facts from the document
- The dataset used for training contains about **36 million sentence pairs**.
- Tokens were split into a **32,000 word-piece vocabulary**.
- The paper reports a **BLEU score of 41.8** for the Transformer big model on this task.
- The model trained for **3.5 days on 8 GPUs**.
- Compared with previous single-model systems, the Transformer achieved a new state of the art at a fraction of the training cost.

## Related concepts and entities
- [[concepts/transformer-models]]
- [[concepts/attention-mechanisms]]
- [[entities/wmt-2014]]
- [[entities/google]]
- [[entities/google-brain]]
- [[entities/google-research]]
- [[entities/tensor2tensor]]
- [[summaries/attention-is-all-you-need]]

## Notes
In the paper, this benchmark is paired with the WMT 2014 English-German task as the main translation evaluation setting.