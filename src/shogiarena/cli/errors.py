"""CLI-specific exception types."""


class CliError(RuntimeError):
    """Base exception for CLI command failures."""

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


class CliArgumentError(CliError):
    """Raised when user-provided arguments are invalid."""


class CliExecutionError(CliError):
    """Raised when an operation fails after argument validation."""
