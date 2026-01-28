from pathlib import Path
from datetime import datetime
from typing import Any

from .models import (
    Artifact,
    ArtifactType,
    generate_id,
)
from ..database import db


class ArtifactManager:
    
    def __init__(self, base_dir: Path | None = None):
        self._base_dir = base_dir or Path.home() / ".autowrkers" / "artifacts"
        self._base_dir.mkdir(parents=True, exist_ok=True)

    def _get_artifact_path(self, workflow_id: str, artifact_id: str, name: str) -> Path:
        workflow_dir = self._base_dir / workflow_id
        workflow_dir.mkdir(parents=True, exist_ok=True)
        
        safe_name = "".join(c if c.isalnum() or c in ".-_" else "_" for c in name)
        return workflow_dir / f"{artifact_id}_{safe_name}"

    def create(
        self,
        workflow_execution_id: str,
        phase_execution_id: str,
        artifact_type: ArtifactType,
        name: str,
        content: str,
        metadata: dict[str, Any] | None = None,
    ) -> Artifact:
        artifact_id = generate_id()
        file_path = self._get_artifact_path(workflow_execution_id, artifact_id, name)
        
        file_path.write_text(content, encoding="utf-8")
        
        artifact = Artifact(
            id=artifact_id,
            workflow_execution_id=workflow_execution_id,
            phase_execution_id=phase_execution_id,
            artifact_type=artifact_type,
            name=name,
            content=content,
            file_path=str(file_path),
            metadata=metadata or {},
        )
        
        db.create_artifact(artifact.to_dict())
        return artifact

    def get(self, artifact_id: str) -> Artifact | None:
        data = db.get_artifact(artifact_id)
        if not data:
            return None
        return Artifact.from_dict(data)

    def get_by_workflow(self, workflow_execution_id: str) -> list[Artifact]:
        data = db.get_artifacts_by_workflow(workflow_execution_id)
        return [Artifact.from_dict(d) for d in data]

    def get_by_phase(self, phase_execution_id: str) -> list[Artifact]:
        data = db.get_artifacts_by_phase(phase_execution_id)
        return [Artifact.from_dict(d) for d in data]

    def get_latest_by_type(
        self,
        workflow_execution_id: str,
        artifact_type: ArtifactType,
    ) -> Artifact | None:
        artifacts = self.get_by_workflow(workflow_execution_id)
        matching = [a for a in artifacts if a.artifact_type == artifact_type]
        if not matching:
            return None
        return max(matching, key=lambda a: a.created_at)

    def update_content(self, artifact_id: str, content: str) -> bool:
        artifact = self.get(artifact_id)
        if not artifact:
            return False
        
        if artifact.file_path:
            Path(artifact.file_path).write_text(content, encoding="utf-8")
        
        return db.update_artifact(artifact_id, {
            "content": content,
            "is_edited": True,
            "updated_at": datetime.now().isoformat(),
        })

    def read_content(self, artifact_id: str) -> str | None:
        artifact = self.get(artifact_id)
        if not artifact:
            return None
        
        if artifact.file_path and Path(artifact.file_path).exists():
            return Path(artifact.file_path).read_text(encoding="utf-8")
        
        return artifact.content

    def delete(self, artifact_id: str) -> bool:
        artifact = self.get(artifact_id)
        if artifact and artifact.file_path:
            file_path = Path(artifact.file_path)
            if file_path.exists():
                file_path.unlink()
        
        return db.delete_artifact(artifact_id)

    def cleanup_workflow(self, workflow_execution_id: str) -> int:
        artifacts = self.get_by_workflow(workflow_execution_id)
        count = 0
        
        for artifact in artifacts:
            if self.delete(artifact.id):
                count += 1
        
        workflow_dir = self._base_dir / workflow_execution_id
        if workflow_dir.exists() and not any(workflow_dir.iterdir()):
            workflow_dir.rmdir()
        
        return count

    def get_artifact_summary(self, workflow_execution_id: str) -> dict[str, Any]:
        artifacts = self.get_by_workflow(workflow_execution_id)
        
        by_type: dict[str, list[dict[str, Any]]] = {}
        total_size = 0
        
        for a in artifacts:
            type_name = a.artifact_type.value
            if type_name not in by_type:
                by_type[type_name] = []
            
            size = len(a.content) if a.content else 0
            total_size += size
            
            by_type[type_name].append({
                "id": a.id,
                "name": a.name,
                "size": size,
                "is_edited": a.is_edited,
                "created_at": a.created_at,
            })
        
        return {
            "workflow_execution_id": workflow_execution_id,
            "total_artifacts": len(artifacts),
            "total_size_bytes": total_size,
            "by_type": by_type,
        }


artifact_manager = ArtifactManager()
