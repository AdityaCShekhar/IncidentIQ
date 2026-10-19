from incidentiq.agent.output import InvestigationReport
from incidentiq.ingestion.schemas import EvidenceBundle


def validate_evidence_references(
    report: InvestigationReport, bundle: EvidenceBundle
) -> InvestigationReport:
    """Resolve IDs deterministically. This does not establish semantic claim support."""
    known = {record.id for record in bundle.records}
    if len(known) != len(bundle.records):
        raise ValueError("Duplicate evidence IDs in the investigation bundle")
    if any(record.incident_id != bundle.incident_id for record in bundle.records):
        raise ValueError("Evidence belongs to a different incident")
    for hypothesis in report.hypotheses:
        references = set(hypothesis.supporting_evidence_ids + hypothesis.contradicting_evidence_ids)
        if not references <= known:
            raise ValueError("Diagnosis references nonexistent evidence")
        if hypothesis.cause != "unknown" and not hypothesis.supporting_evidence_ids:
            raise ValueError("A material cause hypothesis requires supporting evidence")
    if report.disposition == "likely_cause_found" and report.hypotheses[0].cause == "unknown":
        raise ValueError("A likely cause cannot be unknown")
    return report
