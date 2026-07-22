### **Derived capabilities a credible system needs to not have bottlenecks:**

**Context overflow / attention degradation (Approach 0):**

If a system can process a fixed amount of information for one query, no matter how long the conversation becomes. Then it would solve the context overflow problem.

**Wholesale loss of old-but-important decisions (Approach 1):**

An ideal system would summarize old conversations after a certain amount of conversation has happened in a chat to keep the old context.

**No write-side judgment, no ranking beyond similarity, no conflict resolution (Approach 2):**

a) To empower the system with write-side judgement, there could be a filter/LLM call between extraction and storage of memories which will decide whether something is worth storing or not & only gets stored if it's important.

b) While ranking the chunks/nuggets, more weightage should be given to nuggets which are being used the most to generate responses and have recent information as it could contain updates, new findings which are very important to consider while generating an up-to-date response.

c) For conflict handling, A memory system needs more than semantic similarity.
It also needs to represent relationships between memories, such as supersedes,replaces,deprecate, current version, active decision
Without those relationships, semantic retrieval cannot tell whether two highly similar memories should be combined or whether one has made the other obsolete.
