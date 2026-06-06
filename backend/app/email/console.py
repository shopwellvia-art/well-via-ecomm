import logging

logger = logging.getLogger("app.email")


def send_console(*, to: str, subject: str, body: str) -> None:
    """Dev email backend — logs metadata only (never the body).

    The body is intentionally excluded from the log line: it may contain
    cleartext OTPs or password-reset tokens that must not appear in log
    aggregators or CI output. Developers who need to read the body should
    attach a debugger or temporarily write it to a local file outside the
    logging pipeline.
    """
    logger.info(
        "EMAIL (console) to=%s subject=%s body_len=%d [body redacted]",
        to,
        subject,
        len(body),
    )
