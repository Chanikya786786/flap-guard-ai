"""
Persistent Database Storage for Trained ML Artifacts.
Guarantees retrained FlapGuard AI models survive ephemeral Render container restarts.
"""

import json
from typing import Dict, Optional

from ml_engine.model_service import load_model_from_bytes, serialize_model_bytes
from web_app.models import ModelArtifact, db


def persist_active_model_to_db(model, metadata: Dict[str, object]) -> ModelArtifact:
    """Serialize and store the promoted calibrated model artifact as the active DB record."""
    blob, sha256_hex = serialize_model_bytes(model)
    ModelArtifact.query.filter(ModelArtifact.is_active.is_(True)).update({"is_active": False})
    record = ModelArtifact(
        version=str(metadata.get("version", "v2.0"))[:20],
        sha256_checksum=sha256_hex,
        metadata_json=json.dumps(metadata),
        artifact_blob=blob,
        is_active=True,
    )
    db.session.add(record)
    db.session.commit()
    return record


def restore_active_model_from_db() -> Optional[str]:
    """
    On server startup, restore the latest active ModelArtifact from PostgreSQL/SQLite
    if present and checksum-verified. Returns restored version string or None.
    """
    try:
        active = (
            ModelArtifact.query.filter(ModelArtifact.is_active.is_(True))
            .order_by(ModelArtifact.id.desc())
            .first()
        )
        if active is None:
            return None
        metadata = json.loads(active.metadata_json)
        ok = load_model_from_bytes(
            blob=active.artifact_blob,
            metadata=metadata,
            expected_sha256=active.sha256_checksum,
        )
        return active.version if ok else None
    except Exception:
        db.session.rollback()
        return None
