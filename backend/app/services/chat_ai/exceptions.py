class ChatGenerationError(Exception):
    """Base error for provider-neutral chat generation failures."""


class ChatGenerationTimeoutError(ChatGenerationError):
    """Raised when the complete provider call exceeds its deadline."""


class ChatGenerationUnavailableError(ChatGenerationError):
    """Raised when the provider is temporarily unavailable."""


class ChatGenerationConfigurationError(ChatGenerationError):
    """Raised for invalid or unsupported provider configuration."""


class ChatGenerationInvalidResponseError(ChatGenerationError):
    """Raised when the provider response cannot produce one valid chat answer."""
