---
query: "How does the Transformer replace recurrence with self-attention, and what does that buy in training speed?"
---

The Transformer removes recurrence entirely and uses stacked self-attention in the encoder and decoder instead. In place of step-by-step RNN computation, each layer attends directly over all tokens, with positional encoding added so the model still knows order.  

What that buys in training speed is mainly **more parallelism**: because self-attention has **constant sequential depth** per layer, the model can process tokens much more in parallel than recurrent models, which must proceed step by step. The wiki summary also notes that this leads to **shorter paths for long-range dependencies** and **faster training**, with the paper emphasizing substantially better training parallelization.  

In short: **recurrence is replaced by attention over the whole sequence, which makes training much more parallel and therefore faster**.
