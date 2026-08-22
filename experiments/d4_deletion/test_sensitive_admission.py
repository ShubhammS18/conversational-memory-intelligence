
# Sensitive-memory admission


def admit_memory(content, sensitive=False, temporary=False, uncertain=False):
    if sensitive:
        return "REJECT"

    if uncertain:
        return "DON'T_STORE"

    if temporary:
        return "TEMPORARY_STORE"

    return "DURABLE_STORE"



# Case 1 — Sensitive information must be rejected

result = admit_memory(
    "My API key is sk-example-secret",
    sensitive=True)

assert result == "REJECT"

print("Case 1 — sensitive information rejected: PASS")



# Case 2 — Rejected information must not become memory

memories = []

candidate = "My API key is sk-example-secret"

decision = admit_memory(
    candidate,
    sensitive=True)

if decision != "REJECT":
    memories.append(candidate)

assert candidate not in memories

print("Case 2 — rejected information not stored: PASS")


# Case 3 — Temporary information is not durable

decision = admit_memory(
    "For this experiment, use Qdrant.",
    temporary=True)

assert decision == "TEMPORARY_STORE"

print("Case 3 — temporary information classified separately: PASS")


# Case 4 — Uncertain information is not a confirmed
# preference

decision = admit_memory(
    "I'm considering switching to Qdrant.",
    uncertain=True)

assert decision == "DON'T_STORE"

print("Case 4 — uncertain information not stored: PASS")


# Case 5 — Normal durable memory remains admissible

decision = admit_memory(
    "I currently prefer Qdrant.")

assert decision == "DURABLE_STORE"

print("Case 5 — normal durable memory admitted: PASS")