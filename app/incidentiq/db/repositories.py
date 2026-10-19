from uuid import UUID

from sqlalchemy import func, insert, select
from sqlalchemy.orm import Session

from incidentiq.api.schemas import CollectionSummary, IncidentCreate
from incidentiq.core.errors import APIError
from incidentiq.db.models import EvidenceRow, Incident
from incidentiq.ingestion.schemas import EvidenceBundle


class IncidentRepository:
    def __init__(self, session: Session):
        self.session = session

    def create(self, data: IncidentCreate) -> Incident:
        incident = Incident(
            **data.model_dump(exclude={"related_services"}),
            related_services=list(data.related_services),
            status="collecting",
        )
        with self.session.begin():
            self.session.add(incident)
            self.session.flush()
        return incident

    def get(self, incident_id: UUID) -> Incident:
        incident = self.session.get(Incident, incident_id)
        if incident is None:
            raise APIError(404, "incident_not_found", "Incident not found")
        return incident

    def save_bundle(self, incident_id: UUID, bundle: EvidenceBundle) -> Incident:
        if bundle.incident_id != incident_id or any(
            record.incident_id != incident_id for record in bundle.records
        ):
            raise ValueError("Evidence incident scope mismatch")
        with self.session.begin():
            incident = self.session.scalar(
                select(Incident).where(Incident.id == incident_id).with_for_update()
            )
            if incident is None:
                raise APIError(404, "incident_not_found", "Incident not found")
            saved = dict(
                self.session.execute(
                    select(EvidenceRow.content_hash, EvidenceRow.id)
                    .where(EvidenceRow.incident_id == incident_id)
                    .limit(1001)
                ).all()
            )
            additions = []
            for record in bundle.records:
                if record.content_hash in saved:
                    if saved[record.content_hash] != record.id:
                        raise ValueError("Evidence identity mismatch")
                    continue
                additions.append(record.model_dump())
                saved[record.content_hash] = record.id
            if len(saved) > 1000:
                raise ValueError("Incident evidence limit exceeded")
            if additions:
                self.session.execute(insert(EvidenceRow), additions)
            incident.evidence_count = len(saved)
            incident.status = "ready" if saved else "failed"
            incident.collection_status = bundle.status
            incident.collection_metadata = CollectionSummary(
                status=bundle.status,
                limits=bundle.limits,
                coverage=bundle.coverage,
                issues=bundle.issues,
                record_count=len(bundle.records),
            ).model_dump(mode="json")
        return incident

    def evidence_page(
        self, incident_id: UUID, *, service=None, source=None, severity=None, limit=50, offset=0
    ):
        self.get(incident_id)
        predicates = [EvidenceRow.incident_id == incident_id]
        for column, value in (
            (EvidenceRow.service, service),
            (EvidenceRow.source_type, source),
            (EvidenceRow.severity, severity),
        ):
            if value is not None:
                predicates.append(column == value)
        total = self.session.scalar(
            select(func.count()).select_from(EvidenceRow).where(*predicates)
        )
        records = self.session.scalars(
            select(EvidenceRow)
            .where(*predicates)
            .order_by(EvidenceRow.timestamp, EvidenceRow.id)
            .limit(limit)
            .offset(offset)
        ).all()
        return records, total
