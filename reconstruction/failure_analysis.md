## **Failure Analysis**


**Approach 0:**

**Assumption:** The model is capable of attending & weighing all the prior information equally regardless of how much of it there is.

The most intuitive fix to solve the system not remembering the past conversation is to provide every past thing as the context to the system.
Now, the model remembers everything we have discussed in the past and gives good answers. 

**Bottleneck:** This Approach works at the initial stages of the conversation but as the conversations grows, the context starts to become huge, The chat starts to hang & retrieval from long texts becomes difficult for the system which results in important things getting lost or de-prioritized.

And that's exactly what I experienced while building the RAG Project(1st problem). I started a Conversation with chatGPT, planned my project & Started working on it. ChatGPT gave me good results in no time initially but as the chat started to grow, it started to take more time to respond and the results quality started to degrade. 


**Approach 1: Sliding Window**

**Assumption:** Most recent & updated information given as context produces accurate results without lagging.

Since dumping everything in context leads to chat getting hanged & retrieval degradation problem. We can only take the past 'N' no. of messages in the chat as a context and we keep this sliding window as we go deep in the conversation/chat.
This way, the most recently updated things gets chosen as context for generating a response every time and since it is a limited context every time, It solves the retrieval degradation from long context & conversation getting hanged problem.

**Bottleneck:** Although it is solving our 1st problem & The recent, most fresh things are available to us as context, that itself causes a problem here. Because, we are discarding the old conversation altogether. So the important things in those old conversations are not getting considered in the context which would be very important for creating an accurate response. 

To relate this bottleneck with my RAG project and what i experienced, The chat was starting to become big, then somewhere in the project, A code block that i wrote were throwing an error, i shared it with the chat & asked it to help me debug, it then corrected the code and i got a particular output. But, then i realised, it gave more emphasis on correcting that code(i.e. recent context) and produced results which were not aligned with the initial plans of this project. (i.e. started to forget old context)


**Approach 2: Naive Retrieval & Semantic Similarity** 

**Assumption:** retrieving important nuggets of information based on semantic similarity (embeddings) is sufficient to reconstruct the correct and relevant project context.

The primary issue with a sliding window is the permanent loss of information, as critical project decisions are discarded. To overcome this, a better strategy is to move toward an extraction-based approach. Instead of relying on raw message history, the system can identify and isolate key "nuggets" of information such as design decisions or project updates and store them separately. To prevent this stored data from overwhelming the model, we can use semantic embeddings. By embedding both incoming requests and the stored nuggets, the system can perform a similarity search to retrieve only the information relevant to the current task. This ensures that essential context is preserved without cluttering the system with unnecessary data.

**Bottleneck:** We’re doing Naive retrieval here, means everything extracted gets stored, no judgement on what’s actually worth keeping. Similarly, Ranking is the only similarity here & there is no extra importance given for recency or how often something’s been useful. Also, there is no mechanism here to handle conflicts or ambiguity. 

Although chatGPT does not use naive retrieval & is accurate 99% of the time, something similar to this bottleneck i experienced in my RAG project because of the chat getting longer. In the first versions (v1/v2), we decided to store embeddings and chunks together in a single pickle file. 
The design was:     vector_store.pkl
                                     ├── embeddings
                                     └── chunks

In a later version (v3.2), we decided to introduce FAISS and decided to abandon the previous storage format. But, during later conversations about embeddings, it said
"Your project stores embeddings in vector_store.pkl, although later versions also use a FAISS index."  which was wrong because we have abandoned the vector_store.pkl.

