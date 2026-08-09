"""Outgoing mail for invites and password resets.

Stdlib smtplib only - three short templates do not justify a dependency.

Every entry point takes an explicit MailConfig rather than reading global state.
Sends happen on a background task after the response has been returned, where
there is no request-scoped database session to read settings from, so the
caller resolves the config while it still has one.

Mail is optional. With no host configured ``send_mail`` reports False and the
callers fall back to handing the link to the admin in the UI, so a self-hosted
install with no relay still works end to end.
"""

from __future__ import annotations

import logging
import smtplib
import ssl as ssl_module
from email.message import EmailMessage
from email.utils import formataddr
from html import escape

from app.services.mail_settings import MailConfig

logger = logging.getLogger(__name__)


def send_mail(
    cfg: MailConfig, to_address: str, subject: str, text_body: str, html_body: str = ""
) -> bool:
    """Send one message. Returns False (with a log) instead of raising.

    A failed invite mail must not roll back the user that was just created -
    the admin can always copy the link out of the UI instead.
    """
    if not cfg.host.strip():
        logger.warning("Mail not configured; skipped %r to %s", subject, to_address)
        return False

    sender = cfg.resolved_from
    if not sender:
        logger.warning("No from-address configured; cannot send %r", subject)
        return False

    message = EmailMessage()
    message["Subject"] = subject
    message["From"] = formataddr((cfg.from_name, sender))
    message["To"] = to_address
    message.set_content(text_body)
    if html_body:
        message.add_alternative(html_body, subtype="html")

    try:
        if cfg.ssl:
            # Implicit TLS (usually port 465). STARTTLS does not apply here.
            with smtplib.SMTP_SSL(
                cfg.host,
                cfg.port,
                timeout=cfg.timeout,
                context=ssl_module.create_default_context(),
            ) as client:
                _authenticate(client, cfg)
                client.send_message(message)
        else:
            with smtplib.SMTP(cfg.host, cfg.port, timeout=cfg.timeout) as client:
                if cfg.starttls:
                    client.starttls(context=ssl_module.create_default_context())
                _authenticate(client, cfg)
                client.send_message(message)
    except (smtplib.SMTPException, OSError, ssl_module.SSLError) as exc:
        logger.warning("Failed to send %r to %s: %s", subject, to_address, exc)
        return False

    logger.info("Sent %r to %s", subject, to_address)
    return True


def describe_failure(cfg: MailConfig, to_address: str) -> str:
    """Attempt a send and return a human-readable reason on failure.

    Used by the "send test email" button, where the whole point is to surface
    the SMTP error rather than swallow it into the log.
    """
    if not cfg.host.strip():
        return "No SMTP host is configured."
    if not cfg.resolved_from:
        return "No from-address is configured."

    message = EmailMessage()
    message["Subject"] = "Sandstorm Server Manager test email"
    message["From"] = formataddr((cfg.from_name, cfg.resolved_from))
    message["To"] = to_address
    message.set_content(
        "Mail is configured correctly. Invitations and password resets will be "
        "delivered from this address.\n"
    )

    try:
        if cfg.ssl:
            with smtplib.SMTP_SSL(
                cfg.host,
                cfg.port,
                timeout=cfg.timeout,
                context=ssl_module.create_default_context(),
            ) as client:
                _authenticate(client, cfg)
                client.send_message(message)
        else:
            with smtplib.SMTP(cfg.host, cfg.port, timeout=cfg.timeout) as client:
                if cfg.starttls:
                    client.starttls(context=ssl_module.create_default_context())
                _authenticate(client, cfg)
                client.send_message(message)
    except smtplib.SMTPAuthenticationError as exc:
        return f"The relay rejected the username or password ({exc.smtp_code})."
    except smtplib.SMTPRecipientsRefused:
        return f"The relay refused the recipient address {to_address}."
    except smtplib.SMTPSenderRefused:
        return f"The relay refused the from-address {cfg.resolved_from}."
    except ssl_module.SSLError as exc:
        return f"TLS failed: {exc}. Check the STARTTLS / implicit TLS setting and the port."
    except smtplib.SMTPServerDisconnected:
        return (
            "The server disconnected unexpectedly. This usually means the "
            "TLS mode does not match the port."
        )
    except (smtplib.SMTPException, OSError) as exc:
        return f"Could not reach {cfg.host}:{cfg.port} - {exc}"

    return ""


def _authenticate(client: smtplib.SMTP, cfg: MailConfig) -> None:
    if cfg.user and cfg.password:
        client.login(cfg.user, cfg.password)


# --------------------------------------------------------------------------
# Templates
# --------------------------------------------------------------------------


def _wrap_html(heading: str, paragraph: str, link: str, button_label: str) -> str:
    # Escape every dynamic fragment so a display name containing HTML cannot
    # inject markup into recipients' mail clients.
    safe_heading = escape(heading)
    safe_paragraph = escape(paragraph)
    safe_link = escape(link, quote=True)
    safe_label = escape(button_label)
    return (
        '<div style="font-family:system-ui,-apple-system,Segoe UI,sans-serif;'
        'max-width:520px;margin:0 auto;color:#161b22">'
        f'<h2 style="margin:0 0 12px">{safe_heading}</h2>'
        f'<p style="margin:0 0 20px;line-height:1.5">{safe_paragraph}</p>'
        f'<p style="margin:0 0 20px"><a href="{safe_link}" '
        'style="background:#e8a23a;color:#161b22;padding:10px 18px;border-radius:6px;'
        f'text-decoration:none;font-weight:600">{safe_label}</a></p>'
        '<p style="margin:0;font-size:12px;color:#57606a">'
        f"If the button does not work, paste this into your browser:<br>{safe_link}</p></div>"
    )


def send_invite(
    cfg: MailConfig, to_address: str, link: str, inviter: str, ttl_hours: int
) -> bool:
    text = (
        f"{inviter} invited you to Sandstorm Server Manager.\n\n"
        f"Set your password here (link expires in {ttl_hours} hours):\n{link}\n\n"
        "If you were not expecting this invitation you can ignore this message.\n"
    )
    html = _wrap_html(
        "You have been invited",
        f"{inviter} invited you to Sandstorm Server Manager. "
        f"This link expires in {ttl_hours} hours.",
        link,
        "Set your password",
    )
    return send_mail(cfg, to_address, "Your Sandstorm Server Manager invitation", text, html)


def send_password_reset(cfg: MailConfig, to_address: str, link: str, ttl_minutes: int) -> bool:
    text = (
        "Someone requested a password reset for your Sandstorm Server Manager "
        f"account.\n\nReset it here (link expires in {ttl_minutes} minutes):\n{link}\n\n"
        "If this was not you, no action is needed - your password has not changed.\n"
    )
    html = _wrap_html(
        "Reset your password",
        "Someone requested a password reset for your account. "
        f"This link expires in {ttl_minutes} minutes. "
        "If it was not you, no action is needed.",
        link,
        "Reset password",
    )
    return send_mail(cfg, to_address, "Reset your Sandstorm Server Manager password", text, html)
