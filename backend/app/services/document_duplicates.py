"""Shared semantic duplicate scoring and version recommendations."""

from dataclasses import asdict, dataclass

from app.services.fingerprints import signature_similarity, title_similarity


@dataclass(slots=True, frozen=True)
class DuplicateAssessment:
    same_arxiv: bool
    identity_score: float
    content_score: float
    combined_score: float
    qualifies: bool

    def to_dict(self) -> dict[str, bool | float]:
        return asdict(self)


def assess_duplicate(
    *,
    current_arxiv_id: str | None,
    current_title: str | None,
    current_signature: list[int],
    existing_arxiv_id: str | None,
    existing_title: str | None,
    existing_signature: list[int],
) -> DuplicateAssessment:
    """Apply the production duplicate thresholds to one document pair."""

    same_arxiv = bool(current_arxiv_id and current_arxiv_id == existing_arxiv_id)
    identity_score = 1.0 if same_arxiv else title_similarity(current_title, existing_title)
    content_score = signature_similarity(current_signature, existing_signature)
    combined_score = 0.55 * identity_score + 0.45 * content_score
    qualifies = (same_arxiv and content_score >= 0.30) or (
        identity_score >= 0.92 and content_score >= 0.55
    )
    return DuplicateAssessment(
        same_arxiv=same_arxiv,
        identity_score=round(identity_score, 4),
        content_score=round(content_score, 4),
        combined_score=round(combined_score, 4),
        qualifies=qualifies,
    )


def recommend_version(current_version: int | None, existing_version: int | None) -> str:
    if current_version and existing_version:
        if current_version < existing_version:
            return (
                f"库中已有更新的 arXiv v{existing_version}，"
                f"建议保留现有版本并将本次 v{current_version} 作为历史版本。"
            )
        if current_version > existing_version:
            return (
                f"当前文件是更新的 arXiv v{current_version}，"
                f"建议用它替换库中的 v{existing_version}。"
            )
    return "两份文献正文高度重合，请确认覆盖现有版本或同时保留。"
