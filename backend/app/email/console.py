import logging

logger = logging.getLogger("app.email")


def send_console(
    *, to: str, subject: str, body: str, html: str | None = None
) -> None:
    """Dev email backend — logs metadata only (never the body or HTML).

    The body and HTML are intentionally excluded from the log line: they may
    contain cleartext OTPs or password-reset tokens that must not appear in log
    aggregators or CI output. Developers who need to read the content should
    attach a debugger or temporarily write it to a local file outside the
    logging pipeline.
    """
    logger.info(
        "EMAIL (console) to=%s subject=%s body_len=%d html_len=%s [body redacted]",
        to,
        subject,
        len(body),
        len(html) if html else "none",
    )
