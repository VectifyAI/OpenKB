---
sources: ["summaries/attention-is-all-you-need.md"]
type: "Event"
description: "The WMT 2014 English-German machine translation benchmark."
---

# WMT 2014 English-German

## What it is

WMT 2014 English-German is a machine translation benchmark used to evaluate sequence-to-sequence models. In [[summaries/attention-is-all-you-need]], it is one of the two main tasks used to test the [[concepts/transformer-models|Transformer]].

## Key facts from the paper

- The dataset contains about **4.5 million sentence pairs**.
- Sentences were encoded using **byte-pair encoding** with a shared source-target vocabulary of about **37,000 tokens**.
- Training batches were formed by approximate sequence length and contained about **25,000 source tokens** and **25,000 target tokens**.
- The paper reports results on the **newstest2014** test set.
- The Transformer achieved **28.4 BLEU** with the big model, exceeding prior systems by more than 2 BLEU.
- The base model also outperformed previously published models at much lower training cost.

## Role in the paper

This benchmark is the primary English-to-German evaluation used to demonstrate that attention-only architectures can outperform recurrent and convolutional sequence models. It serves as the main evidence for the effectiveness of [[concepts/attention-mechanisms]] in machine translation.

## Related pages

- [[summaries/attention-is-all-you-need]]
- [[concepts/transformer-models]]
- [[concepts/attention-mechanisms]]
- [[entities/wmt-2014]]
- [[entities/tensor2tensor]]