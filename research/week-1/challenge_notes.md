# Challenge Notes

## Review context

The goal here is to check the assumptions we made earlier, identify weak reasoning and check whether the proposed design changes were justified before moving towards implementation.

---

## Challenge 1 – Why not implement the complete Zep architecture?

This was one of the first questions that came up because the paper proposes a full temporal knowledge graph instead of lightweight metadata.

After discussing it, I realised that the biggest problem in my system is not the absence of a knowledge graph. The real problem is distinguishing between valid and superseded memories. Lightweight temporal metadata can solve that problem without introducing the additional complexity of maintaining a graph structure.

As a result, I decided to prototype only the temporal metadata idea instead of adopting the complete architecture.

---

## Challenge 2 – Is an admission stage really necessary?

Initially this question made me rethink whether a separate admission stage would overcomplicate the baseline system.

Looking back at the Week 1 scan, I realised that I had already concluded from Mem0 that memory extraction and admission should be separate stages. The deep dive into A-MAC did not change that conclusion. Instead, it provided a much clearer admission policy based on multiple signals such as confidence, novelty, utility and recency.

Because of that, I kept the admission stage and decided to adopt the multi-signal admission policy.

---

## Challenge 3 – Why not implement the complete Memory Worth mechanism?

The discussion highlighted that the Memory Worth paper depends on observing retrieval outcomes over time to update the usefulness of stored memories.

My current baseline system does not yet have that feedback loop. Implementing the complete mechanism would therefore increase complexity without enough supporting infrastructure.

I decided that the better approach is to keep the idea as a prototype for future iterations while adopting the broader principle that lifecycle decisions should be based on evidence rather than fixed expiration rules.

---

## Challenge 4 – Are these ideas supported by evidence or personal interpretation?

This discussion helped me separate ideas taken directly from the papers from design decisions that are my own.

For example, A-MAC provides evidence that structured admission policies improve memory quality, but choosing a simpler version for my conversational memory system is my own engineering decision. Similarly, Zep demonstrates temporal reasoning through a knowledge graph, while my decision to use lightweight temporal metadata is an interpretation aimed at keeping the baseline implementation practical.

Being explicit about this distinction makes the design decisions easier to justify.

---

## Overall outcome

The review did not change the shortlist of ideas, but it helped refine how each idea will be used.

- The A-MAC admission policy will be adopted because it directly strengthens an existing part of the baseline architecture.
- Lightweight temporal metadata from Zep will be prototyped instead of adopting the full temporal knowledge graph.
- Evidence-informed lifecycle management will remain a prototype until the system has enough infrastructure to support it.
