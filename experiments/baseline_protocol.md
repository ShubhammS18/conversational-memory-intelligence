### Hypothesis

Instead of relying only on recent messages, the system should extract useful pieces of information from conversations, store all of them, and retrieve the most similar ones whenever a new query comes in.

I expect this simple approach to work reasonably well for straightforward conversations where there are only a few stored memories and no conflicting information. However, I also expect it to struggle once conversations become longer, memories start changing over time, or multiple similar memories exist.

### Baseline Design

I am not introducing any new ideas here. This baseline is intentionally based on the same Approach 2 that has been discussed during problem framing work initially. 

The baseline follows a very simple pipeline:

1. Every conversation is scanned for candidate memories.
2. Every extracted memory is stored without deciding whether it is actually important.
3. Each stored memory is converted into an embedding.
4. When a new query comes in, the query is embedded and compared against all stored memories using semantic similarity.
5. The top-k most similar memories are retrieved.
6. Those retrieved memories are directly added to the model's context before generating the response.

I think this is a fair baseline because this is probably the first solution many engineers would come up with after realizing that a sliding window permanently loses important information. Semantic similarity already solves one major problem by allowing the system to recall old information without replaying the entire conversation. It is also simple to understand and implement, which makes it a good starting point before introducing more advanced ideas.

### excluded by design:
To keep this a true naive baseline, I am deliberately not adding any extra intelligence. The following capabilities are intentionally excluded:

* No write-side judgement or filtering. Every extracted memory is stored.
* No ranking signals beyond semantic similarity (no recency, importance, or usage frequency).
* No decay, consolidation, or deletion of memories.
* No conflict or supersession handling between contradictory memories.
* No token-budget-aware context selection. The retrieved top-k memories are directly added to the model context.
* No user or tenant isolation. All memories are stored and retrieved from a single shared memory pool.


### Workload Design

The workload is designed to evaluate the naive conversational memory baseline under different situations that are likely to occur in real conversations. Wherever possible, I reused scenarios that I actually experienced while building my Local RAG project. For the remaining cases, I created realistic synthetic conversations because those situations did not naturally occur during development but are still important for evaluating a conversational memory system.


| Case Type                                                   | Scenario                                                                                                                                                                                                                                                                                                                                                                                                                             | Expected Baseline Failure                                                                                                                                                                                                                                                       |
| ----------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| **1. Irrelevant and contradictory memories**                | **Synthetic:** During previous conversations, the user says **"I switched to a new apartment last month."** Later, the user asks **"Should I switch my memory storage from FAISS to PostgreSQL?"** Because both contain the word *"switch"*, the apartment memory may be retrieved even though it is completely unrelated. In the same conversation history, In the same conversation history, both vector_store.pkl and FAISS storage decisions are present. | The baseline relies only on semantic similarity, so it can retrieve both irrelevant memories and contradictory memories without knowing which ones should actually be used.                                                                                                     |
| **2. Preferences that change over time**                    | **Real:** During my Local RAG project, the storage design changed from vector_store.pkl to FAISS, and eventually vector_store.pkl was removed completely.                                                                                                                                                                                                                                                                        | Similarity-only retrieval cannot recognize that the newer architectural decision supersedes the older one, so both decisions may be retrieved together.                                                                                                                         |
| **3. Long conversations with a constrained context budget** | **Real:** Simulated long-running conversation volume (parameters defined in Experimental Configuration below), representing the point in my Local RAG project where earlier architectural decisions became difficult to keep inside the model's context window.                        | Since the baseline has no context budgeting, it simply injects the retrieved top-k memories. As memory grows, retrieved context occupies more of the available prompt, leaving less room for the current conversation and increasing the chance of incomplete or noisy context. |
| **4. Multiple users with similarly-worded information**     | **Synthetic:** Two different users discuss Python web applications. One prefers FastAPI while the other prefers Flask. Both users' memories are stored in the same memory collection.                                                                                                                                                                                                                                                | The baseline has no user or tenant isolation, so memories belonging to one user can be retrieved while answering another user's query.                                                                                                                                          |
| **5. Sensitive information that should not be retained**    | **Synthetic:** During a conversation, a user shares a temporary API key and later says it should not be remembered. Since the baseline stores every extracted memory, the API key is retained like any other memory.                                                                                                                                                                                                                 | The baseline performs no write-side filtering, so sensitive information is stored even when it should have been discarded.                                                                                                                                                      |
| **6. Cold-start / no relevant memory**                      | **Synthetic:** A brand-new user asks a question about **travel recommendations**, while the memory store only contains conversations about **machine learning projects**. The retriever still returns the top-k most similar memories because no similarity threshold exists.                                                                                                                                                        | The baseline injects unrelated memories into the prompt instead of recognizing that there is no relevant memory and returning an empty result.                                                                                                                                  |



### Experimental Configuration

To make the baseline reproducible, all experiments will use the same workload throughout the evaluation.

- Embedding model: sentence-transformers/all-mpnet-base-v2
- Retrieval method: Top-k semantic similarity
- Top-k: 10
- Dataset: Fixed synthetic + real workload described above
- Random seed: 10
- Memory storage: Single shared memory collection
- Context construction: Retrieved memories followed by the current conversation
- Similarity threshold: none — retriever always returns top-k regardless of relevance score
- Cases where the total memory pool is smaller than the retrieval top-k will be padded with unrelated filler memories. This ensures that ranking behavior is observable and retrieved memories are selected based on similarity rather than simply returning the entire memory pool.
- Filler memories bypass the extraction step and are injected directly into storage, since their content requires no interpretation. They are used only to create sufficient ranking pressure during retrieval experiments.
- Workload conversations may include user_id metadata to represent multiple users. The naive baseline ignores this metadata and stores all extracted memories in a single shared memory pool.
- The user_id identifies the conversation/session that a turn belongs to, not the speaker itself. Assistant responses therefore carry the same user_id as the user they are responding to.