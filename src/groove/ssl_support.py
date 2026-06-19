"""Configure process-wide SSL for urllib (used by musicbrainzngs).

On macOS, Python often has no usable default CA bundle, so HTTPS to
musicbrainz.org fails with CERTIFICATE_VERIFY_FAILED.  Pointing the default
HTTPS context at certifi's bundle matches what httpx / yt-dlp already do.
"""

from __future__ import annotations

import logging
import ssl

log = logging.getLogger(__name__)
_configured = False


def configure_default_ssl_context() -> None:
    """Idempotent: set ssl._create_default_https_context to use certifi CA file."""
    global _configured
    if _configured:
        return
    _configured = True
    try:
        import certifi
    except ImportError:
        log.warning(
            "certifi is not installed; MusicBrainz HTTPS may fail with "
            "CERTIFICATE_VERIFY_FAILED on some systems (install certifi or run "
            "Install Certificates.command from python.org)."
        )
        return
    try:
        def _https_context() -> ssl.SSLContext:
            return ssl.create_default_context(cafile=certifi.where())

        ssl._create_default_https_context = _https_context  # type: ignore[method-assign]
    except Exception as exc:
        log.warning("Could not apply certifi SSL defaults: %s", exc)
