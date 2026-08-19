class GuideGenerationError(Exception):
    """Base error for provider-neutral guide generation failures."""


class GuideGenerationInputError(GuideGenerationError):
    """Raised when confirmed prescription data cannot satisfy the AI input contract."""


class GuideGenerationTimeoutError(GuideGenerationError):
    """Raised when the complete provider call exceeds its deadline."""


class GuideGenerationUnavailableError(GuideGenerationError):
    """Raised when the provider is temporarily unavailable."""


class GuideGenerationConfigurationError(GuideGenerationError):
    """Raised for missing or unsupported provider configuration."""


class GuideGenerationInvalidResponseError(GuideGenerationError):
    """Raised when a single valid structured provider response cannot be established."""


class GuideGenerationSafetyError(GuideGenerationError):
    """Raised when generated text cannot be safely published."""

    def __init__(self, rule_id: str) -> None:
        self.rule_id = rule_id
        super().__init__(f"Guide generation was blocked by safety rule: {rule_id}")
