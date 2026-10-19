"""The cross-vendor worker match (AB10, D55, PRD PD1, C5.10).

Workers are pseudonymous to Anthropic; their vendors hold their identities. To notice the same
person at two vendors without learning who they are, Anthropic gives every vendor one key, and a
vendor submits, for each worker, HMAC-SHA256(key, the worker's email address, trimmed and
lowercased). The platform stores only that hash, so it can match workers across vendors but can't
recover an identity, and nobody without the key can test a guess. A match is evidence for a
person to weigh, never an action on its own, and a vendor is never told which vendor matched.
"""

import uuid

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

MATCHES = text("""
SELECT o.name AS vendor_name, u.handle, mu.handle AS matched_handle, mo.name AS matched_vendor,
       (mu.suspended_at IS NOT NULL) AS matched_suspended
FROM worker_identities i JOIN users u ON u.id = i.worker_id JOIN orgs o ON o.id = u.org_id
JOIN worker_identities m ON m.hash = i.hash AND m.worker_id <> i.worker_id
JOIN users mu ON mu.id = m.worker_id JOIN orgs mo ON mo.id = mu.org_id
WHERE i.worker_id = :w AND mu.org_id <> u.org_id
""")


async def matches(session: AsyncSession, worker_id: uuid.UUID) -> list[dict]:
    """Workers at other vendors who share this worker's hash. Anthropic only."""
    return [dict(r._mapping) for r in await session.execute(MATCHES, {"w": worker_id})]
