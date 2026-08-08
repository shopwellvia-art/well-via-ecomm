"""One customer's activity, assembled from rows that already exist.

There is no `customer_events` table and this service deliberately does not add
one. Every question the 360 view answers — what they bought, returned, reviewed,
earned, wished for, abandoned, wrote in, and what staff did to their account —
is already recorded by the feature that caused it. What was missing was a single
ordered read across all of them.

HOW IT WORKS
------------
Two passes, on purpose:

  1. A UNION ALL over the source tables projecting only
     `(occurred_at, kind, ref_id)` — three uniform columns, so MySQL never has
     to coerce a DECIMAL against an INT against a VARCHAR across branches. This
     pass does the ordering and the pagination and nothing else.
  2. The page's ids, grouped by kind, are hydrated with one small query per kind
     present (at most nine, over at most `page_size` rows).

Splitting it this way keeps the ordering query narrow and lets each event carry
a genuinely useful summary instead of whatever string the union could agree on.

PAGINATION IS OFFSET-BASED, NOT KEYSET
--------------------------------------
MySQL `DATETIME` here has no fractional-seconds precision, and several of these
rows are written inside one transaction — placing an order writes the order, a
cart event and a points transaction within the same second. Ties at a page
boundary are therefore likely, not theoretical, and a keyset cursor on
`occurred_at` would silently drop them. One customer's history is small enough
that OFFSET costs nothing.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from sqlalchemy import String, func, literal, select, union_all
from sqlalchemy.orm import Session

from app.models.analytics_facts import CartEvent
from app.models.audit import AuditEvent
from app.models.contact_message import ContactMessage
from app.models.loyalty import PointsTransaction
from app.models.order import Order
from app.models.product import Product
from app.models.referral import Referral
from app.models.return_request import ReturnRequest
from app.models.review import Review
from app.models.user import User
from app.models.wishlist import Wishlist

#: Event kinds, in the order they are declared in the union. Also the allowlist
#: for the `kinds` filter — an unknown kind selects nothing rather than
#: quietly returning everything.
KINDS = (
    "order",
    "return",
    "review",
    "points",
    "referral_made",
    "referral_received",
    "wishlist",
    "cart",
    "contact",
    "admin_action",
)


@dataclass
class ActivityEvent:
    occurred_at: datetime
    kind: str
    ref_id: int
    title: str
    detail: str | None = None
    meta: dict[str, Any] = field(default_factory=dict)


class CustomerActivityService:
    """Reader for one customer's activity feed.

    `show_money` controls whether per-event amounts (order totals, cart values)
    are rendered. It is NOT cosmetic: four order totals on a feed sum to roughly
    the lifetime spend that `analytics.customers.view` exists to withhold, so
    leaving them on for a caller who cannot see `gross_ltv` would reopen the
    side door the customer directory closes. Callers pass True when the actor
    holds `analytics.customers.view` OR `orders.view_all` — the latter because
    someone allowed to read every order is already allowed to read these.
    """

    def __init__(self, db: Session, *, show_money: bool = False):
        self.db = db
        self.show_money = show_money

    # ---- Public API ------------------------------------------------------

    def timeline(
        self,
        user: User,
        *,
        kinds: list[str] | None = None,
        offset: int = 0,
        limit: int = 50,
    ) -> tuple[list[ActivityEvent], int]:
        """Return `(events, total)` for this customer, newest first."""
        selected = self._selected_kinds(kinds)
        if not selected:
            return [], 0

        branches = [
            b for kind, b in self._branches(user).items() if kind in selected
        ]
        if not branches:
            return [], 0

        unioned = union_all(*branches).subquery("activity")
        total = self.db.execute(
            select(func.count()).select_from(unioned)
        ).scalar_one()

        rows = self.db.execute(
            select(unioned.c.occurred_at, unioned.c.kind, unioned.c.ref_id)
            .order_by(unioned.c.occurred_at.desc(), unioned.c.ref_id.desc())
            .offset(offset)
            .limit(limit)
        ).all()
        return self._hydrate(user, rows), total

    def counters(self, user: User) -> dict[str, int]:
        """Per-kind totals for the 360 header. One grouped query over the same
        union, so a tab's badge can never disagree with the tab's contents."""
        unioned = union_all(*self._branches(user).values()).subquery("activity")
        rows = self.db.execute(
            select(unioned.c.kind, func.count()).group_by(unioned.c.kind)
        ).all()
        counts = {kind: 0 for kind in KINDS}
        for kind, n in rows:
            counts[str(kind)] = int(n)
        return counts

    # ---- The union -------------------------------------------------------

    def _selected_kinds(self, kinds: list[str] | None) -> set[str]:
        if not kinds:
            return set(KINDS)
        return {k for k in kinds if k in set(KINDS)}

    def _branches(self, user: User):
        """One SELECT per source, all projecting (occurred_at, kind, ref_id).

        `literal(...).cast(String)` pins the kind column's type so MySQL does not
        infer a different width per branch and truncate the longest label.
        """
        uid = user.id

        def branch(ts_col, kind: str, id_col, where):
            return select(
                ts_col.label("occurred_at"),
                literal(kind).cast(String(24)).label("kind"),
                id_col.label("ref_id"),
            ).where(where)

        return {
            "order": branch(Order.created_at, "order", Order.id, Order.user_id == uid),
            "return": branch(
                ReturnRequest.created_at,
                "return",
                ReturnRequest.id,
                ReturnRequest.user_id == uid,
            ),
            # user_id is nullable on reviews (staff-authored ones have none), so
            # this only ever matches reviews the customer actually wrote.
            "review": branch(
                Review.created_at, "review", Review.id, Review.user_id == uid
            ),
            "points": branch(
                PointsTransaction.created_at,
                "points",
                PointsTransaction.id,
                PointsTransaction.user_id == uid,
            ),
            "referral_made": branch(
                Referral.created_at,
                "referral_made",
                Referral.id,
                Referral.referrer_user_id == uid,
            ),
            "referral_received": branch(
                Referral.created_at,
                "referral_received",
                Referral.id,
                Referral.referred_user_id == uid,
            ),
            "wishlist": branch(
                Wishlist.created_at, "wishlist", Wishlist.id, Wishlist.user_id == uid
            ),
            "cart": branch(
                CartEvent.occurred_at, "cart", CartEvent.id, CartEvent.user_id == uid
            ),
            # contact_messages has no user_id — only the address the sender
            # typed. This is an EMAIL MATCH, not a foreign key, and the API
            # labels it as such: a message sent from a different address will
            # not appear here.
            "contact": branch(
                ContactMessage.created_at,
                "contact",
                ContactMessage.id,
                ContactMessage.email == user.email,
            ),
            # Actions staff took ON this account, not actions it took.
            "admin_action": branch(
                AuditEvent.created_at,
                "admin_action",
                AuditEvent.id,
                (AuditEvent.target_type == "user") & (AuditEvent.target_id == uid),
            ),
        }

    # ---- Hydration -------------------------------------------------------

    def _hydrate(self, user: User, rows) -> list[ActivityEvent]:
        by_kind: dict[str, list[int]] = {}
        for _, kind, ref_id in rows:
            by_kind.setdefault(str(kind), []).append(int(ref_id))

        loaded = {
            kind: self._load(kind, ids) for kind, ids in by_kind.items()
        }

        events: list[ActivityEvent] = []
        for occurred_at, kind, ref_id in rows:
            kind = str(kind)
            built = loaded.get(kind, {}).get(int(ref_id))
            if built is None:
                # The row vanished between the union and the hydrate (a delete
                # mid-page). Skip it rather than rendering a blank card.
                continue
            title, detail, meta = built
            events.append(
                ActivityEvent(
                    occurred_at=occurred_at,
                    kind=kind,
                    ref_id=int(ref_id),
                    title=title,
                    detail=detail,
                    meta=meta,
                )
            )
        return events

    def _load(self, kind: str, ids: list[int]) -> dict[int, tuple[str, str | None, dict]]:
        """id -> (title, detail, meta) for one kind."""
        if kind == "order":
            rows = self._rows(Order, ids)

            def order_detail(o):
                status = o.status.value if hasattr(o.status, "value") else o.status
                if not self.show_money:
                    return str(status)
                return f"{o.currency} {o.total_amount} · {status}"

            return {
                o.id: (
                    f"Placed order {o.order_number}",
                    order_detail(o),
                    {"order_id": o.id, "order_number": o.order_number,
                     "status": str(o.status.value if hasattr(o.status, "value") else o.status)},
                )
                for o in rows
            }
        if kind == "return":
            rows = self._rows(ReturnRequest, ids)
            return {
                r.id: (
                    f"Requested a return on order #{r.order_id}",
                    r.reason,
                    {"return_id": r.id, "order_id": r.order_id,
                     "status": str(r.status.value if hasattr(r.status, "value") else r.status)},
                )
                for r in rows
            }
        if kind == "review":
            rows = self._rows(Review, ids)
            names = self._product_names([r.product_id for r in rows])
            return {
                r.id: (
                    f"Reviewed {names.get(r.product_id, 'a product')} — {r.rating}★",
                    r.title or r.body,
                    {"product_id": r.product_id, "rating": r.rating,
                     "approved": bool(r.is_approved)},
                )
                for r in rows
            }
        if kind == "points":
            rows = self._rows(PointsTransaction, ids)
            return {
                p.id: (
                    f"{'Earned' if p.delta >= 0 else 'Spent'} {abs(p.delta)} points",
                    p.description or str(p.reason),
                    {"delta": p.delta, "reason": str(p.reason)},
                )
                for p in rows
            }
        if kind in ("referral_made", "referral_received"):
            rows = self._rows(Referral, ids)
            made = kind == "referral_made"
            return {
                r.id: (
                    "Referred a friend" if made else "Joined via a referral",
                    f"Code {r.code} · {r.status.value if hasattr(r.status, 'value') else r.status}",
                    {"code": r.code,
                     "status": str(r.status.value if hasattr(r.status, "value") else r.status)},
                )
                for r in rows
            }
        if kind == "wishlist":
            rows = self._rows(Wishlist, ids)
            names = self._product_names([w.product_id for w in rows])
            return {
                w.id: (
                    f"Saved {names.get(w.product_id, 'a product')} to the wishlist",
                    None,
                    {"product_id": w.product_id},
                )
                for w in rows
            }
        if kind == "cart":
            rows = self._rows(CartEvent, ids)
            names = self._product_names([c.product_id for c in rows if c.product_id])
            return {
                c.id: (
                    f"Cart: {c.event_type}",
                    names.get(c.product_id) if c.product_id else None,
                    {"event_type": str(c.event_type), "product_id": c.product_id,
                     "value": (
                         str(c.value)
                         if (c.value is not None and self.show_money)
                         else None
                     )},
                )
                for c in rows
            }
        if kind == "contact":
            rows = self._rows(ContactMessage, ids)
            return {
                m.id: (
                    f"Wrote in: {m.subject or 'no subject'}",
                    m.message,
                    # Flagged so the UI can say "matched by email address".
                    {"email_matched": True, "status": str(m.status)},
                )
                for m in rows
            }
        if kind == "admin_action":
            rows = self._rows(AuditEvent, ids)
            return {
                a.id: (
                    a.summary or a.action,
                    f"by {a.actor_email or 'system'}",
                    {"action": a.action, "actor": a.actor_email},
                )
                for a in rows
            }
        return {}

    def _rows(self, model, ids: list[int]):
        if not ids:
            return []
        return list(
            self.db.execute(select(model).where(model.id.in_(ids))).scalars().all()
        )

    def _product_names(self, product_ids: list[int]) -> dict[int, str]:
        ids = [p for p in product_ids if p]
        if not ids:
            return {}
        rows = self.db.execute(
            select(Product.id, Product.name).where(Product.id.in_(ids))
        ).all()
        return {r[0]: r[1] for r in rows}
