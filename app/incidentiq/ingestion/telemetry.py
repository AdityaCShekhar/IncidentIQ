import heapq
import json
import os
import re
import stat
from collections import Counter
from pathlib import Path
from time import monotonic
from uuid import NAMESPACE_URL, UUID, uuid5

from incidentiq.ingestion.normalize import canonical, normalize, observations
from incidentiq.ingestion.schemas import (
    CollectionIssue,
    CollectionLimits,
    EvidenceBundle,
    EvidenceWindow,
    SourceCoverage,
)

FILE_NAME = re.compile(r"^(traces|logs|metrics)(?:-\d{4}-\d\d-\d\dT[\d.-]+)?\.jsonl$")
SOURCES = {"traces": "trace", "logs": "log", "metrics": "metric"}
INVALID = (ValueError, TypeError, KeyError, AttributeError, OverflowError, RecursionError)


def reject_nonfinite(value):
    raise ValueError("Non-finite JSON literal")


def collect_evidence(
    directory: Path,
    window: EvidenceWindow,
    *,
    incident_id: UUID | None = None,
    limits: CollectionLimits | None = None,
) -> EvidenceBundle:
    """Read fixed-size file snapshots without network, shell, SQL, or following symlinks."""
    limits = limits or CollectionLimits()
    incident_id = incident_id or uuid5(
        NAMESPACE_URL, "incidentiq:window:v1:" + canonical(window.model_dump(mode="json"))
    )
    coverage = {source: SourceCoverage() for source in SOURCES.values()}
    issues = Counter()
    deadline = monotonic() + limits.deadline_seconds
    kept, seen, heap = {}, set(), []
    bytes_read = lines_read = observations_read = 0
    stopped = False

    def issue(code, source=None):
        issues[(code, source)] += 1

    files = []
    try:
        if directory.is_symlink():
            raise OSError("Symlink source directory")
        with os.scandir(directory) as entries:
            for index, entry in enumerate(entries):
                if monotonic() >= deadline:
                    issue("deadline_exceeded")
                    stopped = True
                    break
                if index >= 256:
                    issue("directory_entry_limit")
                    break
                match = FILE_NAME.fullmatch(entry.name)
                if match:
                    files.append((SOURCES[match[1]], entry.name))
    except OSError:
        issue("directory_unavailable")
    discovered = {source for source, _ in files}
    for source in coverage:
        if source not in discovered:
            issue("missing_source_files", source)
    # Traces first; active file before sorted rotated files for each source.
    rank = {"trace": 0, "log": 1, "metric": 2}
    files.sort(key=lambda item: (rank[item[0]], "-" in item[1], item[1]))
    if len(files) > limits.max_files:
        issue("file_limit")
        files = files[: limits.max_files]

    for source, filename in files:
        if stopped:
            break
        if monotonic() >= deadline:
            issue("deadline_exceeded", source)
            break
        stats = coverage[source]
        try:
            flags = os.O_RDONLY | os.O_NONBLOCK | os.O_NOFOLLOW
            descriptor = os.open(directory / filename, flags)
            with os.fdopen(descriptor, "rb") as stream:
                metadata = os.fstat(stream.fileno())
                if not stat.S_ISREG(metadata.st_mode):
                    issue("unsafe_source_file", source)
                    continue
                stats.files_scanned += 1
                remaining = metadata.st_size
                line_number = 0
                while remaining > 0:
                    if monotonic() >= deadline:
                        issue("deadline_exceeded", source)
                        stopped = True
                        break
                    if bytes_read >= limits.max_scan_bytes or lines_read >= limits.max_lines:
                        issue("scan_limit", source)
                        stopped = True
                        break
                    capacity = min(
                        limits.max_line_bytes + 1, limits.max_scan_bytes - bytes_read, remaining
                    )
                    raw = stream.readline(capacity)
                    if not raw:
                        issue("source_changed_during_read", source)
                        break
                    remaining -= len(raw)
                    bytes_read += len(raw)
                    stats.bytes_scanned += len(raw)
                    line_number += 1
                    lines_read += 1
                    stats.lines_scanned += 1
                    if len(raw) > limits.max_line_bytes:
                        issue("line_size_limit", source)
                        break  # Do not interpret the rest of an oversized line as another record.
                    if not raw.endswith(b"\n") and remaining > 0:
                        issue("scan_limit", source)
                        stopped = True
                        break
                    try:
                        document = json.loads(raw, parse_constant=reject_nonfinite)
                        for observation in observations(document, source):
                            if monotonic() >= deadline:
                                issue("deadline_exceeded", source)
                                stopped = True
                                break
                            if observations_read >= limits.max_observations:
                                issue("observation_limit", source)
                                stopped = True
                                break
                            observations_read += 1
                            stats.observations_scanned += 1
                            try:
                                record = normalize(
                                    observation,
                                    window,
                                    incident_id,
                                    f"{filename}:{line_number}#{observation.pointer}",
                                )
                            except INVALID:
                                issue("invalid_observation", source)
                                continue
                            if record is None:
                                continue
                            if record.id in seen:
                                stats.duplicates += 1
                                continue
                            seen.add(record.id)
                            order = (
                                -int(record.payload["timestamp_unix_nano"]),
                                -record.id.int,
                                record.id,
                            )
                            if len(kept) >= limits.max_records:
                                issue("record_limit", source)
                                if order <= heap[0]:
                                    continue
                                del kept[heapq.heappop(heap)[2]]
                            heapq.heappush(heap, order)
                            kept[record.id] = record
                    except INVALID + (UnicodeError,):
                        issue("invalid_json_or_envelope", source)
                    if stopped:
                        break
        except OSError:
            issue("source_unreadable", source)

    records = sorted(
        kept.values(),
        key=lambda record: (int(record.payload["timestamp_unix_nano"]), record.id.int),
    )
    for record in records:
        physical_source = "log" if record.source_type == "deployment" else record.source_type
        coverage[physical_source].records_collected += 1
    if not any(stats.files_scanned for stats in coverage.values()):
        status = "unavailable"
    elif issues:
        status = "partial"
    else:
        status = "collected" if records else "empty"
    return EvidenceBundle(
        incident_id=incident_id,
        window=window,
        status=status,
        limits=limits,
        records=records,
        coverage=coverage,
        issues=[
            CollectionIssue(code=code, source_type=source, count=count)
            for (code, source), count in sorted(
                issues.items(), key=lambda item: (item[0][0], item[0][1] or "")
            )
        ],
    )
