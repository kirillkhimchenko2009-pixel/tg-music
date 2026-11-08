"""Ошибки музыкальной системы с кодами и признаком retryable."""
from __future__ import annotations


class MusicError(Exception):
    code = "MUSIC_ERROR"
    retryable = False

    def __init__(self, message: str = "", *, code: str | None = None,
                 retryable: bool | None = None):
        super().__init__(message or self.__class__.__name__)
        if code:
            self.code = code
        if retryable is not None:
            self.retryable = retryable


class ProviderError(MusicError):
    code = "PROVIDER_ERROR"


class ProviderUnavailable(ProviderError):
    code = "PROVIDER_UNAVAILABLE"
    retryable = True


class ProviderRateLimited(ProviderError):
    code = "PROVIDER_RATE_LIMITED"
    retryable = True


class ProviderAuthError(ProviderError):
    code = "PROVIDER_AUTH_REQUIRED"


class NotSupportedError(ProviderError):
    code = "NOT_SUPPORTED"


class NotFoundError(ProviderError):
    code = "NOT_FOUND"


class PermissionDenied(MusicError):
    code = "PERMISSION_DENIED"


class DownloadError(MusicError):
    code = "DOWNLOAD_ERROR"
    retryable = True


class FileTooLarge(DownloadError):
    code = "FILE_TOO_LARGE"
    retryable = False


class InvalidContent(DownloadError):
    code = "INVALID_CONTENT"
    retryable = False


class SSRFBlocked(MusicError):
    code = "SSRF_BLOCKED"


class ProcessingError(MusicError):
    code = "PROCESSING_ERROR"


class QueueFullError(MusicError):
    code = "QUEUE_FULL"
    retryable = True


class UserLimitError(MusicError):
    code = "USER_LIMIT"


class CancelledError(MusicError):
    code = "CANCELLED"


class NoSpaceError(MusicError):
    code = "NO_SPACE"
