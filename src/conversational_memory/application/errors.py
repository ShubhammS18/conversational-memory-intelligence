"""Application-layer technical and validation errors."""


class ApplicationError(Exception):
    """Base class for errors exposed by application workflows."""


class ValidationError(ApplicationError):
    """A request cannot safely proceed."""

    def __init__(self, reason: str) -> None:
        self.reason = reason
        super().__init__(reason)


class StorageError(ApplicationError):
    """The authoritative store could not complete an operation."""


class IndexingError(ApplicationError):
    """Embedding-index persistence could not complete an operation."""
