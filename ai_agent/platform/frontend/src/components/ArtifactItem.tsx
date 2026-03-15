import type { Artifact } from "../types";

export function ArtifactItem({ artifact }: { artifact: Artifact }) {
  return (
    <li>
      <div>
        <strong>{artifact.artifact_name}</strong>
        <div className="muted">
          {artifact.artifact_type} v{artifact.version}
        </div>
      </div>
      <a className="artifact-link" href={artifact.file_url}>
        Open
      </a>
    </li>
  );
}
