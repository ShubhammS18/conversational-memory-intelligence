# Design Backlog

## Purpose

This document records architectural ideas, mechanisms and research directions that were considered during the design of the Conversational Memory Intelligence System but intentionally deferred from the baseline implementation. The purpose of maintaining this backlog is to document these decisions explicitly so that future iterations of the system can build upon them without repeating the same design discussions.

## Deferred Design Ideas

During the literature scan, I came across several ideas that looked useful but were intentionally kept outside the baseline implementation. Some of them solve problems that my current system does not face yet, while others require much more complex infrastructure than is needed for this project. I have recorded them here so they can be revisited in future iterations instead of repeating the same research.

### Privacy-preserving Retrieval

Several papers explored encrypting stored memories or performing retrieval without exposing the original data. These techniques are important for production systems handling sensitive user information, but my baseline system does not store real user PII and privacy was deliberately scoped out earlier. Because of this, I decided not to include encrypted retrieval or different privacy mechanisms in the initial design.

### Fine-grained Access Control and Multi-tenant Isolation

Some research focused on ensuring that users can only retrieve their own memories through role-based permissions and tenant-aware indexing. Since the baseline implementation is designed for a single-user environment, these mechanisms would add significant complexity without improving the current system. They remain valuable if the project is extended into a multi-user conversational memory platform.


### Production Monitoring and Cost Governance

Some recent papers discussed ideas such as per-user cost attribution, detailed audit logs and advanced observability for memory systems. These are useful for operating large-scale production systems but are outside the scope of the baseline implementation. Once the core architecture has been validated, these ideas can be considered as future improvements.