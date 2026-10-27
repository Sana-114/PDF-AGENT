"use client";

import type { DocumentPeople, PeopleSource } from "../lib/api";

interface Props {
  data: DocumentPeople;
  activeBlockId?: string;
  onSelectSource: (source: PeopleSource, label: string) => void;
}

export default function DocumentPeoplePanel({ data, activeBlockId, onSelectSource }: Props) {
  function sourceButtons(sources: PeopleSource[], label: string) {
    return (
      <div className="pdf-people-sources" aria-label={`${label}原文来源`}>
        {sources.map((source, index) => {
          const suffix = index === 0 ? "" : source.page_number !== sources[index - 1].page_number
            ? "（续）" : ` · 片段 ${index + 1}`;
          return (
            <button
              aria-label={`${label} · 第 ${source.page_number} 页原文${suffix}`}
              aria-pressed={activeBlockId === source.block_id}
              data-source-block-id={source.block_id}
              data-source-page={source.page_number}
              key={`${source.page_number}:${source.block_id}`}
              onClick={() => onSelectSource(source, label)}
              type="button"
            >
              第 {source.page_number} 页{suffix}
            </button>
          );
        })}
      </div>
    );
  }

  return (
    <div className="pdf-people-panel">
      <section aria-label="论文署名">
        <h3>论文署名</h3>
        {data.authors.length ? <p>{data.authors.join("；")}</p> : <p>未提取到署名。</p>}
        {sourceButtons(data.author_sources, "论文署名")}
        {data.authors.length > 0 && data.author_sources.length === 0 && (
          <p className="pdf-people-note">署名尚无可用坐标，请核对 PDF 首页。</p>
        )}
      </section>
      {data.affiliations.length > 0 && (
        <section aria-label="机构信息">
          <h3>机构信息</h3>
          <p>{data.affiliations.join("；")}</p>
          <p className="pdf-people-note">按原文提取，未推定各人与机构的对应关系。</p>
        </section>
      )}
      <section aria-label="贡献分组">
        <h3>贡献分组</h3>
        {data.contributor_groups.length > 0 && (
          <>
            <p className="pdf-people-note">以下为角色成员，不是全局作者顺序。同一人可能出现在多个分组。</p>
            <p className="pdf-people-note">仅合并换行，重音字符沿用 PDF 文本层，请以原页为准。</p>
            {data.contributor_groups.map((group) => (
              <details key={group.role} data-contributor-role={group.role}>
                <summary>{group.role} <small>{group.members.length} 项</small></summary>
                {sourceButtons(group.sources, group.role)}
                <p className="pdf-contributor-members">{group.members.join("；")}</p>
              </details>
            ))}
          </>
        )}
        {data.contributor_status === "legacy" && (
          <p className="pdf-people-note">旧解析结果未包含贡献分组；如需查看，请重新解析。</p>
        )}
        {data.contributor_status === "not_detected" && (
          <p className="pdf-people-note">未检测到独立的贡献分组。</p>
        )}
        {data.contributor_status === "unverified" && (
          <p className="pdf-people-note" role="status">部分贡献分组的原文来源不完整，未展示这些分组。请重新解析后核对。</p>
        )}
      </section>
    </div>
  );
}
