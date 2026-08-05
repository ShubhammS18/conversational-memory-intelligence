# Research Landscape

## Purpose

This document captures the overall research landscape explored while designing the Conversational Memory Intelligence System. It summarizes the main research areas investigated and the key architectural ideas identified during the initial literature scan.

The Week 1 research focused on understanding the major system components required for a conversational memory system and identifying how current research addresses each of those components. This document will continue to evolve as deeper validation is carried out in later stages of the project and additional papers are explored.


## Research Areas Explored

### 1. Memory Model and Representation

The literature showed that modern conversational memory systems increasingly organize memories into different categories such as working, episodic and semantic memory instead of treating all stored information the same. Many systems also distinguish between temporary conversational context and long-term memories, allowing the memory store to evolve independently of the model's context window. This reinforced the idea that our system should treat memory as a structured knowledge layer rather than simply storing past conversations.

### 2. Attention and Context

The research explored how memory should interact with the model's limited context window. Instead of relying on long conversation histories, recent systems retrieve only the most relevant information and construct the prompt dynamically. This supported our earlier design decision that retrieved memories should complement the live conversation rather than replace it.

### 3. Memory Extraction and Admission

Several papers focused on deciding what information deserves to become a long-term memory instead of storing every conversation. Common ideas included salience, confidence, memory typing, provenance and deduplication. This directly aligned with one of the major open questions in our system regarding memory write decisions.

### 4. Retrieval Augmentation

The research showed that retrieval has evolved beyond simple vector search into hybrid retrieval pipelines combining dense retrieval, reranking and query reformulation. Another common theme was grounding retrieved memories with supporting evidence to improve answer reliability. These ideas reinforced retrieval as one of the core capabilities of the conversational memory system.

### 5. Index and Storage Layer

The papers explored different approaches for efficiently storing and searching memories using approximate nearest neighbour indexes such as FAISS and HNSW. They also discussed trade-offs involving recall, latency, storage cost and incremental updates. This validated the design direction of using a dedicated vector index rather than sequential memory search.

### 6. Ranking and Conflict Resolution

The literature demonstrated that retrieval quality depends not only on finding similar memories but also on ranking them using factors such as relevance, recency, confidence. Several works also highlighted the need to recognise when newer information supersedes older memories. This directly relates to one of the most important unresolved problems identified in our project.

### 7. Context Construction

Modern conversational memory systems increasingly build prompts by combining live conversation with carefully selected memories instead of maximising the amount of retrieved context. Research emphasised memory hierarchy, context budgeting, ordering and grounding as important factors for maintaining response quality within limited context windows.

### 8. Reflection and Lifecycle

The papers showed that conversational memory extends beyond storing information by managing memories throughout their lifecycle. Reflection, consolidation, summarisation, correction and forgetting were recurring themes for maintaining memory quality over long periods while avoiding uncontrolled memory growth.

### 9. Evaluation and Observability

The literature highlighted that conversational memory systems should be evaluated as complete systems rather than only measuring retrieval accuracy. Recent work increasingly measures retrieval quality, response quality, end-to-end utility, latency, cost and failure cases together, allowing system improvements to be validated more systematically.

### 10. Privacy, Safety and Isolation

The research explored techniques for protecting stored memories through privacy-preserving retrieval, access control and tenant isolation. While these mechanisms are essential for production systems handling real user information, they were intentionally scoped out of the baseline implementation so that the initial design could focus on memory correctness and core functionality.


## Overall Observations

The literature consistently showed that conversational memory systems are evolving from simple retrieval pipelines into complete memory management systems. Rather than relying on a single retrieval step, modern architectures combine memory extraction, structured storage, retrieval, ranking, context construction and lifecycle management to maintain useful long-term memories.

Another clear trend was that memory quality depends on both write-side and read-side decisions. Several papers emphasised that deciding what should become a memory is just as important as retrieving the right memory later, highlighting the importance of memory admission, salience and confidence.

The research also showed that retrieval quality can no longer rely on semantic similarity alone. Modern systems increasingly incorporate ranking signals such as recency, confidence, temporal validity and provenance to improve retrieval quality and resolve conflicting memories.

Finally, production-oriented research places growing emphasis on evaluation, observability, privacy and system reliability. These areas become increasingly important as conversational memory systems move from research prototypes towards real-world deployment.


## Position of the Current System

The Conversational Memory Intelligence System being developed in this project follows a practical, research-informed baseline architecture rather than proposing a completely new memory framework. The goal is to combine well-established ideas from existing conversational memory research into a system that addresses the specific limitations identified during the Local RAG project and the subsequent problem reconstruction.

The baseline design focuses on the core capabilities required for a credible conversational memory system, including selective memory extraction, structured memory storage, retrieval, ranking, context construction and memory lifecycle management. Throughout the systematic problem construction phase, these capabilities were translated into concrete design constraints and open questions that now guide the system architecture.

At the same time, several advanced research directions have intentionally been kept outside the baseline implementation. Areas such as privacy-preserving retrieval, fine-grained access control, encrypted vector search and enterprise-scale multi-tenant infrastructure remain important, but they do not directly affect the initial architecture being implemented. These topics will instead be considered as potential future extensions once the baseline conversational memory system has been validated.


## Future Directions

The Week 1 literature scan provided a broad understanding of the conversational memory research landscape and helped identify the components that require deeper investigation before finalising the system architecture. Based on the confidence ratings and their relevance to the unresolved design questions from problem construction phase, the next stage of research will focus on three shortlisted areas: Memory Extraction and Admission, Ranking and Conflict Resolution, and Reflection and Lifecycle.

The objective of the targeted validation is to move beyond high-level architectural ideas and evaluate specific mechanisms, implementation trade-offs and supporting evidence from the literature. The findings from this deeper investigation will be used to populate the idea evaluation matrix, justify design decisions and refine the baseline architecture before implementation begins.

As the project progresses, this document will continue to be updated with additional research areas, newly published work and architectural insights that influence the evolution of the Conversational Memory Intelligence System.
