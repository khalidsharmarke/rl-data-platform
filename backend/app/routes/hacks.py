"""The reward-hack detector's contract, the recall, and the review queue for weak findings
(TO3, TO6, TO8, D35, D37, D38).

The hack detector is a black box behind the same kind of contract as worker detection (D23):
it reads episodes by sequence number, reports findings with a severity and how far it has
read, and receives decided environment cases as labels. It signs in with the detection token
(one deployment of detectors in this build). What a finding leads to is the platform's:

- case: open (or join) the version's environment case; the version is quarantined at once and,
  if training has used it, a recall goes out (app/environment_cases.py)
- watch: a researcher is asked to read a sample of the version's transcripts (D38)

Training reads recalls with its own token and reports, per run, what it did.
"""

import json
import uuid
from datetime import datetime
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app import clock, environments
from app.abuse import add_action
from app.audit import record
from app.db import get_session
from app.environment_cases import flag_version
from app.models import EnvironmentVersion, Role, User
from app.routes.catalog import require_training
from app.routes.detection import require_detection_service
from app.scoping import require_roles

router = APIRouter()


class HackFindingIn(BaseModel):
    version_id: uuid.UUID
    run_label: str
    rule: str
    rule_version: str
    severity: str = Field(pattern="^(watch|case)$")
    observation: str
    # Expected: episode_ids (a sample), flagged_episodes, first_flagged_step.
    evidence: dict[str, Any]


class HackFindingsIn(BaseModel):
    detector: str
    processed_through: int
    findings: list[HackFindingIn] = []


@router.get("/hack-detection/episodes", dependencies=[Depends(require_detection_service)])
async def episode_feed(after: int = Query(0, ge=0), limit: int = Query(1000, ge=1, le=5000), session: AsyncSession = Depends(get_session)) -> dict:
    rows = await session.execute(text(
        "SELECT e.seq, e.episode_id, e.version_id, r.label AS run_label, e.step, e.reward, e.passed, e.length, e.touched "
        "FROM episodes e JOIN training_runs r ON r.id = e.run_id WHERE e.seq > :a ORDER BY e.seq LIMIT :n"), {"a": after, "n": limit})
    return {"episodes": [{**r._mapping, "version_id": str(r.version_id)} for r in rows]}


@router.get("/hack-detection/progress", dependencies=[Depends(require_detection_service)])
async def hack_progress(detector: str = "hacks-v1", session: AsyncSession = Depends(get_session)) -> dict:
    through = await session.scalar(text("SELECT through_seq FROM hack_detection_progress WHERE detector = :d"), {"d": detector})
    return {"detector": detector, "processed_through": through or 0}


@router.get("/hack-detection/labels", dependencies=[Depends(require_detection_service)])
async def hack_labels(session: AsyncSession = Depends(get_session)) -> dict:
    """Decided environment cases: a retired version needs no more watching; a cleared one starts afresh."""
    rows = await session.execute(text(
        "SELECT subject_id AS version_id, decision, decided_at FROM cases "
        "WHERE subject_type = 'environment_version' AND status = 'decided' ORDER BY decided_at"))
    return {"labels": [{"version_id": str(r.version_id), "decision": r.decision, "at": r.decided_at.isoformat()} for r in rows]}


@router.post("/hack-detection/findings", status_code=status.HTTP_202_ACCEPTED, dependencies=[Depends(require_detection_service)])
async def hack_findings(body: HackFindingsIn, session: AsyncSession = Depends(get_session)) -> dict:
    now = clock.now()
    opened = 0
    for f in body.findings:
        version = await session.get(EnvironmentVersion, f.version_id)
        run_id = await session.scalar(text("SELECT id FROM training_runs WHERE label = :l"), {"l": f.run_label})
        if version is None or run_id is None:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, f"Unknown version or run in finding {f.rule}")
        finding_id = await session.scalar(
            text("INSERT INTO hack_findings (detector, rule, rule_version, severity, version_id, run_id, observation, evidence, "
                 "through_seq, created_at) VALUES (:d, :r, :rv, :s, :v, :run, :o, CAST(:e AS jsonb), :t, :now) "
                 "ON CONFLICT ON CONSTRAINT hack_findings_once DO NOTHING RETURNING id"),
            {"d": body.detector, "r": f.rule, "rv": f.rule_version, "s": f.severity, "v": version.id, "run": run_id,
             "o": f.observation, "e": json.dumps(f.evidence), "t": body.processed_through, "now": now},
        )
        if finding_id is None:
            continue  # delivered again: already handled
        if f.severity == "case":
            had_case = await session.scalar(text("SELECT 1 FROM cases WHERE subject_type = 'environment_version' "
                                                 "AND subject_id = :v AND status = 'open'"), {"v": version.id})
            case_id = await flag_version(session, version, observation=f.observation, actor=None, rule=f.rule,
                                         rule_version=f.rule_version, evidence=f.evidence)
            opened += 1 if case_id and not had_case else 0
        elif version.status == "approved":
            await session.execute(text("INSERT INTO hack_checks (finding_id) VALUES (:f)"), {"f": finding_id})
    await session.execute(
        text("INSERT INTO hack_detection_progress (detector, through_seq, reported_at) VALUES (:d, :t, :now) "
             "ON CONFLICT (detector) DO UPDATE SET through_seq = greatest(hack_detection_progress.through_seq, EXCLUDED.through_seq), "
             "reported_at = EXCLUDED.reported_at"),
        {"d": body.detector, "t": body.processed_through, "now": now},
    )
    await session.commit()
    return {"cases_opened": opened}


# The recall (TO8, D37): training reads it and reports what it did with each run.

class RecallOut(BaseModel):
    id: int
    version_id: uuid.UUID
    environment_name: str
    issued_at: datetime
    runs: list[dict[str, Any]]
    reports: list[dict[str, Any]]


class RecallReportIn(BaseModel):
    run_label: str
    action: str = Field(pattern="^(rolled_back|continued)$")
    checkpoint_step: int | None = Field(default=None, ge=0)
    note: str | None = Field(default=None, max_length=1000)


RECALLS = """
SELECT rc.id, rc.version_id, e.name AS environment_name, rc.issued_at, rc.runs, rc.case_id,
       coalesce((SELECT json_agg(json_build_object('run_label', p.run_label, 'action', p.action, 'checkpoint_step', p.checkpoint_step,
                                                   'note', p.note, 'reported_at', p.reported_at) ORDER BY p.reported_at)
                 FROM recall_reports p WHERE p.recall_id = rc.id), '[]') AS reports
FROM recalls rc JOIN environment_versions v ON v.id = rc.version_id JOIN environments e ON e.id = v.environment_id
"""


def recall_out(r) -> RecallOut:
    return RecallOut(id=r.id, version_id=r.version_id, environment_name=r.environment_name, issued_at=r.issued_at, runs=r.runs, reports=r.reports)


@router.get("/recalls", dependencies=[Depends(require_training)])
async def list_recalls(after: int = Query(0, ge=0), session: AsyncSession = Depends(get_session)) -> list[RecallOut]:
    return [recall_out(r) for r in await session.execute(text(RECALLS + " WHERE rc.id > :a ORDER BY rc.id"), {"a": after})]


@router.post("/recalls/{recall_id}/reports", status_code=status.HTTP_201_CREATED, dependencies=[Depends(require_training)])
async def report_on_recall(recall_id: int, body: RecallReportIn, session: AsyncSession = Depends(get_session)) -> RecallOut:
    """Training says what it did with one run named in a recall; the case records it (PD8)."""
    recall = (await session.execute(text(RECALLS + " WHERE rc.id = :id"), {"id": recall_id})).first()
    if recall is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Not found")
    if body.run_label not in {r["run_label"] for r in recall.runs}:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "That run isn't named in this recall")
    if body.action == "rolled_back" and body.checkpoint_step is None:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "A rollback names the checkpoint step it went back to")
    stored = await session.scalar(
        text("INSERT INTO recall_reports (recall_id, run_label, action, checkpoint_step, note, reported_at) "
             "VALUES (:r, :l, :a, :c, :n, :now) ON CONFLICT ON CONSTRAINT recall_reports_once_per_run DO NOTHING RETURNING id"),
        {"r": recall_id, "l": body.run_label, "a": body.action, "c": body.checkpoint_step, "n": body.note, "now": clock.now()},
    )
    if stored is None:
        raise HTTPException(status.HTTP_409_CONFLICT, "Training already reported on this run")
    what = (f"rolled {body.run_label} back to the checkpoint at step {body.checkpoint_step:,}" if body.action == "rolled_back"
            else f"kept {body.run_label} going")
    await add_action(session, recall.case_id, "report", None, f"Training {what}" + (f": {body.note}" if body.note else ""), clock.now())
    case_vendor = await session.scalar(text("SELECT vendor_id FROM cases WHERE id = :c"), {"c": recall.case_id})
    record(session, actor=None, action="recall.reported", entity_type="recall", entity_id=str(recall_id), org_id=case_vendor,
           after=body.model_dump())
    await session.commit()
    return recall_out((await session.execute(text(RECALLS + " WHERE rc.id = :id"), {"id": recall_id})).first())


@router.get("/cases/{case_id}/recall")
async def case_recall(case_id: uuid.UUID, user: User = Depends(require_roles(Role.DATA_OPS, Role.RESEARCHER)),
                      session: AsyncSession = Depends(get_session)) -> RecallOut | None:
    r = (await session.execute(text(RECALLS + " WHERE rc.case_id = :c"), {"c": case_id})).first()
    return recall_out(r) if r else None


# Weak findings: a researcher reads a sample of transcripts (D38).

class HackCheckOut(BaseModel):
    id: uuid.UUID
    version_id: uuid.UUID
    environment_name: str
    run_label: str
    observation: str
    episodes: list[dict[str, Any]]
    created_at: datetime


class HackCheckIn(BaseModel):
    outcome: str = Field(pattern="^(looks_fine|flagged)$")


@router.get("/hack-checks")
async def open_checks(user: User = Depends(require_roles(Role.RESEARCHER, Role.DATA_OPS)), session: AsyncSession = Depends(get_session)) -> list[HackCheckOut]:
    rows = await session.execute(text(
        "SELECT c.id, f.version_id, e.name AS environment_name, r.label AS run_label, f.observation, f.evidence, f.created_at "
        "FROM hack_checks c JOIN hack_findings f ON f.id = c.finding_id JOIN training_runs r ON r.id = f.run_id "
        "JOIN environment_versions v ON v.id = f.version_id JOIN environments e ON e.id = v.environment_id "
        "WHERE c.outcome IS NULL AND v.status = 'approved' ORDER BY f.created_at"))  # a quarantined version has a case instead
    out = []
    for r in rows:
        ids = list(r.evidence.get("episode_ids", []))[:5]
        eps = await session.execute(text("SELECT episode_id, step, reward, length, transcript_hash FROM episodes "
                                         "WHERE episode_id = ANY(:ids) ORDER BY step"), {"ids": ids})
        episodes = [{"episode_id": e.episode_id, "step": e.step, "reward": e.reward, "length": e.length,
                     "transcript": environments.read(e.transcript_hash).decode(errors="replace") if e.transcript_hash else None} for e in eps]
        out.append(HackCheckOut(id=r.id, version_id=r.version_id, environment_name=r.environment_name, run_label=r.run_label,
                                observation=r.observation, episodes=episodes, created_at=r.created_at))
    return out


@router.post("/hack-checks/{check_id}")
async def answer_check(check_id: uuid.UUID, body: HackCheckIn, user: User = Depends(require_roles(Role.RESEARCHER, Role.DATA_OPS)),
                       session: AsyncSession = Depends(get_session)) -> dict:
    """Looks fine, or flagged (the researcher then opens a case from the version, which is a person's flag)."""
    updated = await session.execute(text("UPDATE hack_checks SET outcome = :o, checked_by = :u, checked_at = :now "
                                         "WHERE id = :id AND outcome IS NULL"),
                                    {"o": body.outcome, "u": user.id, "now": clock.now(), "id": check_id})
    if updated.rowcount == 0:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "No open check with that id")
    await session.commit()
    return {"outcome": body.outcome}
