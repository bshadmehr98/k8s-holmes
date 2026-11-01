"""Reports built from the store (design §10): every number is counted from records, prose is quoted from
findings, revisions and a person's edits stay apart; Markdown and JSON exports; share tokens.

`base` holds what every kind shares, `investigation` the live summary and the investigation report,
`reviews` the incident and reliability reviews; the comparison report lives with the saved investigations.
"""

from k8sholmes.reports.base import (
    Section,
    add_edit,
    create_share,
    revoke_share,
    section,
    shared_report,
    store_report,
    to_markdown,
)
from k8sholmes.reports.investigation import (
    investigation_json,
    investigation_report,
    live_summary,
    report_text,
)
from k8sholmes.reports.reviews import incident_review, reliability_review

__all__ = [
    "Section",
    "add_edit",
    "create_share",
    "incident_review",
    "investigation_json",
    "investigation_report",
    "live_summary",
    "reliability_review",
    "report_text",
    "revoke_share",
    "section",
    "shared_report",
    "store_report",
    "to_markdown",
]
