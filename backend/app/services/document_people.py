"""Compact reader metadata with the same provenance checks as retrieval."""

from app.services.author_evidence import author_evidence_nodes, validated_contributor_groups


def people_metadata(parsed: dict) -> dict:
    groups = validated_contributor_groups(parsed)
    raw_groups = parsed.get("contributor_groups")
    if "contributor_groups" not in parsed:
        status = "legacy"
    elif not isinstance(raw_groups, list) or len(groups) != len(raw_groups):
        status = "unverified"
    else:
        status = "available" if groups else "not_detected"
    # Only actual block IDs are exported, never the synthetic author-metadata ID.
    selected = {(node["page_number"], block_id)
                for node in author_evidence_nodes(parsed) for block_id in node["block_ids"]}
    sources = [{"page_number": page["page_number"], "block_id": block["block_id"],
                "bbox": block["bbox"], "text": block["text"]}
               for page in parsed.get("pages", [])[:3] for block in page.get("blocks", [])
               if (page["page_number"], block.get("block_id")) in selected]

    def names(key: str) -> list[str]:
        values = parsed.get(key, [])
        return [value for value in values if isinstance(value, str) and value.strip()] \
            if isinstance(values, list) else []

    return {"authors": names("authors"), "affiliations": names("affiliations"),
            "author_sources": sources, "contributor_groups": groups,
            "contributor_status": status}
