"""Public session-package validation interface.

The validator remains colocated with the append-only reader implementation for
now so schema-1 reconstruction and schema-2 checksum rules share one parser.
"""

from imagined_speech.recording.session import (
    SessionValidationError,
    SessionValidationReport,
    validate_session,
)

__all__ = ["SessionValidationError", "SessionValidationReport", "validate_session"]
