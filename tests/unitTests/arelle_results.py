"""Arelle results for tests that fake Arelle."""

import json

from mireport.arelle.support import ArelleProcessingResult


def cleanValidation() -> ArelleProcessingResult:
    """A run Arelle finished validating and found nothing wrong in."""
    log = {
        "log": [
            {
                "code": "info",
                "level": "info",
                "message": {"text": "validated in 0.01 secs"},
            }
        ]
    }
    return ArelleProcessingResult.fromArelleLogs(json.dumps(log), [])
