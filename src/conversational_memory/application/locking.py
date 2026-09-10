"""One process-wide lock shared by every durable write workflow."""

from threading import Lock

PROCESS_WRITE_LOCK = Lock()
