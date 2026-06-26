# Original Transformer Paper

The source material for this skill is the paper **Attention Is All You Need**. The distilled worldview is:

- sequence transduction should be built from attention rather than recurrence when you want parallel training and short paths between distant tokens
- self-attention is the mechanism that lets each position read the whole sequence in one layer
- multi-head attention restores expressiveness by letting the model attend in several learned subspaces at once
- positional encoding is mandatory because attention alone does not know order
- masked decoder self-attention preserves autoregressive generation by preventing future-token leakage

The paper’s practical comparison is not “attention vs. everything”; it is a specific engineering trade-off against RNNs and convolutional sequence models. The architecture wins by reducing sequential computation, while still handling alignment, dependency tracking, and output generation through specialized attention blocks.
