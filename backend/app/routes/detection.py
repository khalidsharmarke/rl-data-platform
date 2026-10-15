"""The detection service's contract with the platform (AB13, D23, D24, D25, D32).

Only the detection service can call these routes: it signs in with its own token, not as a
person. Events go out in commit order (app/events.py), findings come back with a severity
and the point in the stream the detector has processed. The platform decides what a finding
leads to: a case-severity finding opens or joins the worker's case and holds their answers;
a watch finding opens a discreet escalation (app/escalation.py), and nobody outside Anthropic
sees it. A finding from a rule version in shadow mode is stored and its answers go to
reviewers, but it leads to nothing else (D41, app/detection_quality.py).
"""

import json
import secrets
import uuid
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel, Field
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app import clock, detection_quality, escalation
from app.abuse import Observation, open_or_join_case
from app.config import settings
from app.db import get_session

router = APIRouter(prefix="/detection")
bearer = HTTPBearer(auto_error=False)


def require_detection_service(credentials: HTTPAuthorizationCredentials | None = Depends(bearer)) -> None:
    expected = settings.detection_token
    if not expected or credentials is None or not secrets.compare_digest(credentials.credentials, expected):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Not the detection service")


class FindingIn(BaseModel):
    worker_id: uuid.UUID
    project_id: uuid.UUID
    rule: str
    rule_version: str
    severity: str = Field(pattern="^(watch|case)$")
    observation: str
    evidence: dict[str, Any]
    through_event: int


class FindingsIn(BaseModel):
    detector: str
    processed_through: int
    findings: list[FindingIn] = []


@router.get("/events", dependencies=[Depends(require_detection_service)])
async def events(after: int = Query(0, ge=0), limit: int = Query(500, ge=1, le=2000), session: AsyncSession = Depends(get_session)) -> dict:
    rows = await session.execute(
        text("SELECT id, payload FROM submission_events WHERE id > :after ORDER BY id LIMIT :limit"), {"after": after, "limit": limit}
    )
    return {"events": [{"id": r.id, **r.payload} for r in rows]}


@router.get("/rules", dependencies=[Depends(require_detection_service)])
async def rules(detector: str = "rules-v1", session: AsyncSession = Depends(get_session)) -> dict:
    """Each known rule version's mode. A version not listed is in shadow until data ops promotes it."""
    return {"detector": detector, "modes": await detection_quality.modes(session, detector)}


@router.get("/outcomes", dependencies=[Depends(require_detection_service)])
async def outcomes(after: int = Query(0, ge=0), session: AsyncSession = Depends(get_session)) -> dict:
    """Reviewers' abuse judgements and cleared escalations, in order (D40)."""
    rows = await session.execute(
        text("SELECT seq, kind, worker_id, project_id, submission_id, outcome, through_event FROM detection_outcomes "
             "WHERE seq > :after ORDER BY seq LIMIT 1000"), {"after": after})
    return {"outcomes": [
        {"seq": r.seq, "kind": r.kind, "worker_id": str(r.worker_id), "project_id": str(r.project_id),
         "submission_id": str(r.submission_id) if r.submission_id else None, "outcome": r.outcome, "through_event": r.through_event}
        for r in rows
    ]}


@router.get("/progress", dependencies=[Depends(require_detection_service)])
async def progress(detector: str = "rules-v1", session: AsyncSession = Depends(get_session)) -> dict:
    through = await session.scalar(text("SELECT processed_through FROM detection_progress WHERE detector = :d"), {"d": detector})
    return {"detector": detector, "processed_through": through or 0}


@router.get("/labels", dependencies=[Depends(require_detection_service)])
async def labels(after: int = Query(0, ge=0), session: AsyncSession = Depends(get_session)) -> dict:
    """Decisions people made on worker cases, with the last event each decision covered (D20)."""
    rows = await session.execute(
        text(
            """
            SELECT a.seq, c.subject_id AS worker_id, c.project_id, a.action, a.created_at,
                   (SELECT max(e.id) FROM submission_events e WHERE e.created_at <= a.created_at) AS through_event
            FROM case_actions a JOIN cases c ON c.id = a.case_id
            WHERE a.seq > :after AND c.subject_type = 'worker'
              AND a.action IN ('clear', 'warn', 'suspend', 'remove', 'reverse')
            ORDER BY a.seq
            """
        ),
        {"after": after},
    )
    return {"labels": [
        {"seq": r.seq, "worker_id": str(r.worker_id), "project_id": str(r.project_id) if r.project_id else None,
         "decision": r.action, "at": r.created_at.isoformat(), "through_event": r.through_event or 0}
        for r in rows
    ]}


@router.post("/findings", status_code=status.HTTP_202_ACCEPTED, dependencies=[Depends(require_detection_service)])
async def findings(body: FindingsIn, session: AsyncSession = Depends(get_session)) -> dict:
    now = clock.now()
    opened = 0
    by_worker: dict[tuple[uuid.UUID, uuid.UUID], list[FindingIn]] = {}
    for f in body.findings:
        by_worker.setdefault((f.worker_id, f.project_id), []).append(f)
    for (worker_id, project_id), group in by_worker.items():
        vendor_id = await session.scalar(text("SELECT org_id FROM users WHERE id = :w AND role = 'worker'"), {"w": worker_id})
        if vendor_id is None:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, f"Unknown worker {worker_id}")
        case_id = None
        live = {f.rule_version: await detection_quality.mode(session, body.detector, f.rule_version) == "live" for f in group}
        cases = [f for f in group if f.severity == "case" and live[f.rule_version]]
        if cases:
            case_id, new = await open_or_join_case(
                session, worker_id=worker_id, vendor_id=vendor_id, project_id=project_id,
                findings=[Observation(f.rule, f.rule_version, f.observation, f.evidence) for f in cases],
            )
            opened += new
            await escalation.end_for_case(session, worker_id, case_id)
        for f in group:
            finding_id = await session.scalar(
                text(
                    "INSERT INTO findings (detector, rule, rule_version, severity, worker_id, vendor_id, project_id, observation, "
                    "evidence, through_event, case_id, shadow, created_at) VALUES (:d, :r, :rv, :sev, :w, :v, :p, :o, "
                    "CAST(:e AS jsonb), :t, :c, :shadow, :now) ON CONFLICT ON CONSTRAINT findings_once DO NOTHING RETURNING id"
                ),
                {"d": body.detector, "r": f.rule, "rv": f.rule_version, "sev": f.severity, "w": worker_id, "v": vendor_id,
                 "p": project_id, "o": f.observation, "e": json.dumps(f.evidence), "t": f.through_event,
                 "c": case_id if f.severity == "case" and live[f.rule_version] else None,
                 "shadow": not live[f.rule_version], "now": now},
            )
            if finding_id:
                await detection_quality.request_reviews(session, finding_id, [str(s) for s in f.evidence.get("submission_ids", [])])
            if not live[f.rule_version]:
                continue  # shadow: recorded and reviewed, nothing more
            held = await session.scalar(text("SELECT 1 FROM holds WHERE worker_id = :w AND released_at IS NULL"), {"w": worker_id})
            if f.severity == "watch" and finding_id and not held:
                await escalation.start(session, finding_id=finding_id, worker_id=worker_id, project_id=project_id,
                                       after_event=f.through_event)
    # Progress only moves forward, even if an older batch arrives late.
    await session.execute(
        text(
            "INSERT INTO detection_progress (detector, processed_through, reported_at) VALUES (:d, :t, :now) "
            "ON CONFLICT (detector) DO UPDATE SET processed_through = greatest(detection_progress.processed_through, "
            "EXCLUDED.processed_through), reported_at = EXCLUDED.reported_at"
        ),
        {"d": body.detector, "t": body.processed_through, "now": now},
    )
    await session.commit()
    return {"cases_opened": opened}
