"""Append-only-ish audit log for acme."""

LOG = []


def audit(event, actor="system"):
    """Record an event. Tests observe module state via audit.LOG."""
    entry = f"{actor}: {event}"
    LOG.append(entry)
    return entry
