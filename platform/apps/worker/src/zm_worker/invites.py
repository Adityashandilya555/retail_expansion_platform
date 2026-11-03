"""Invite emails (S2-23): each ready client's unsent invites, through Resend's REST API.

The API seals each invite's one-time token into `app.invites.sealed` (`zm_api.people`). This sends
`CLIENT_APP_BASE_URL/invite/<token>` from `EMAIL_FROM`, then forgets the token (`sealed` null, so
only its hash is left). Until RESEND_API_KEY, EMAIL_FROM and CLIENT_APP_BASE_URL are all set the
invites wait; a failed send is tried again on the next pass. Tokens never reach the log.
"""

import logging
import os
from collections import Counter

import httpx
from sqlalchemy import text

from zm_api import secrets
from zm_api.context import ClientContext

log = logging.getLogger("zm_worker.invites")

RESEND_URL = "https://api.resend.com/emails"
MAIL_ENV = ("RESEND_API_KEY", "EMAIL_FROM", "CLIENT_APP_BASE_URL")
_warned = False


def client() -> httpx.AsyncClient | None:
    """An HTTP client holding the Resend key; None (said once) while email isn't configured."""
    global _warned
    missing = [v for v in MAIL_ENV if not os.environ.get(v)]
    if missing:
        if not _warned:
            log.warning("invite emails off, invites wait: set %s", " and ".join(missing))
            _warned = True
        return None
    key = os.environ["RESEND_API_KEY"]
    return httpx.AsyncClient(headers={"Authorization": f"Bearer {key}"}, timeout=10)


async def send(ctx: ClientContext, mail: httpx.AsyncClient) -> Counter[str]:
    """Email every unsent, unexpired invite of a still-invited person: counts `sent`, `unsent`."""
    async with ctx.db.connect() as conn:
        rows = (
            await conn.execute(
                text(
                    "select i.id, i.sealed, u.email, u.name from app.invites i"
                    " join app.users u on u.id = i.user_id where i.sealed is not null"
                    " and i.expires_at > now() and u.status = 'invited' order by i.created_at"
                )
            )
        ).all()
    link = os.environ["CLIENT_APP_BASE_URL"].rstrip("/") + "/invite/"
    who = ctx.client
    out: Counter[str] = Counter()
    for row in rows:
        token = secrets.unseal(row.sealed)["token"]
        body = {
            "from": os.environ["EMAIL_FROM"],
            "to": [row.email],
            "subject": f"You're invited to {who.name} on Z-Matrix",
            "text": (
                f"Hello {row.name},\n\nSet your password to join {who.name} on Z-Matrix:\n"
                f"{link}{token}\n\nThe link works once and expires in 7 days. After that, sign in"
                f" with the workspace code {who.workspace_code} and this email address.\n"
            ),
        }
        try:
            # the same key on a retry: Resend sends a twice-posted invite once (within 24 h)
            response = await mail.post(
                RESEND_URL, json=body, headers={"Idempotency-Key": str(row.id)}
            )
            response.raise_for_status()
        except httpx.HTTPError as e:
            status = e.response.status_code if isinstance(e, httpx.HTTPStatusError) else None
            log.warning(
                "invite email not sent (%s)",
                status or type(e).__name__,
                extra={"client": who.slug},
            )
            out["unsent"] += 1
            continue
        async with ctx.db.begin() as conn:
            await conn.execute(
                text("update app.invites set sealed = null, sent_at = now() where id = :id"),
                {"id": row.id},
            )
        out["sent"] += 1
    return out
