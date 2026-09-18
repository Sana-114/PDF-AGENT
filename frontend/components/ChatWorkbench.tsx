"use client";

import { FormEvent, useCallback, useEffect, useRef, useState } from "react";

import {
  ConversationDetail,
  ConversationMessage,
  ConversationSummary,
  createConversation,
  deleteConversation,
  DocumentRecord,
  EvidenceAnchor,
  getConversation,
  listConversations,
  streamConversationMessage,
} from "../lib/api";

interface ChatWorkbenchProps {
  agentLabel: string;
  documents: DocumentRecord[];
  onOpenEvidence: (evidence: EvidenceAnchor) => void;
}

interface StreamPreview {
  content: string;
  claims: ConversationMessage["claims"];
  evidence: EvidenceAnchor[];
  trace: ConversationMessage["trace"];
  model: string | null;
}

const EMPTY_PREVIEW: StreamPreview = {
  content: "",
  claims: [],
  evidence: [],
  trace: [],
  model: null,
};

export default function ChatWorkbench({
  agentLabel,
  documents,
  onOpenEvidence,
}: ChatWorkbenchProps) {
  const [conversations, setConversations] = useState<ConversationSummary[]>([]);
  const [active, setActive] = useState<ConversationDetail | null>(null);
  const [selectedDocument, setSelectedDocument] = useState("all");
  const [question, setQuestion] = useState("");
  const [pendingQuestion, setPendingQuestion] = useState<string | null>(null);
  const [preview, setPreview] = useState<StreamPreview>(EMPTY_PREVIEW);
  const [streamStatus, setStreamStatus] = useState("");
  const [loading, setLoading] = useState(true);
  const [sending, setSending] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const transcriptRef = useRef<HTMLDivElement>(null);

  const refreshHistory = useCallback(async () => {
    try {
      setConversations(await listConversations());
    } catch (requestError) {
      setError(requestError instanceof Error ? requestError.message : "会话历史加载失败");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void refreshHistory();
  }, [refreshHistory]);

  useEffect(() => {
    transcriptRef.current?.scrollTo({
      top: transcriptRef.current.scrollHeight,
      behavior: "smooth",
    });
  }, [active?.messages.length, preview.content, pendingQuestion]);

  async function openConversation(conversationId: string) {
    setError(null);
    setStreamStatus("");
    try {
      setActive(await getConversation(conversationId));
    } catch (requestError) {
      setError(requestError instanceof Error ? requestError.message : "会话读取失败");
    }
  }

  function startConversation() {
    if (sending) return;
    setActive(null);
    setPendingQuestion(null);
    setPreview(EMPTY_PREVIEW);
    setStreamStatus("");
    setError(null);
  }

  async function removeConversation(conversation: ConversationSummary) {
    if (!window.confirm(`确定删除会话“${conversation.title}”及其全部消息吗？`)) return;
    try {
      await deleteConversation(conversation.id);
      if (active?.id === conversation.id) startConversation();
      await refreshHistory();
    } catch (requestError) {
      setError(requestError instanceof Error ? requestError.message : "会话删除失败");
    }
  }

  async function submitQuestion(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const cleanQuestion = question.trim();
    if (!cleanQuestion || sending) return;

    setSending(true);
    setError(null);
    setQuestion("");
    setPendingQuestion(cleanQuestion);
    setPreview(EMPTY_PREVIEW);
    setStreamStatus("正在创建会话…");
    try {
      let conversation = active;
      if (!conversation) {
        conversation = await createConversation(
          selectedDocument === "all" ? [] : [selectedDocument],
        );
        setActive(conversation);
      }
      const turn = await streamConversationMessage(conversation.id, cleanQuestion, {
        onStatus: (payload) => setStreamStatus(payload.message),
        onStart: (payload) => {
          setStreamStatus("正在组织可追溯回答…");
          setPreview((current) => ({ ...current, model: payload.model || payload.provider }));
        },
        onDelta: (text) =>
          setPreview((current) => ({ ...current, content: current.content + text })),
        onClaims: (claims) => setPreview((current) => ({ ...current, claims })),
        onEvidence: (evidence) => setPreview((current) => ({ ...current, evidence })),
        onTrace: (trace) => setPreview((current) => ({ ...current, trace })),
      });
      setActive((current) => ({
        ...turn.conversation,
        messages: [
          ...(current?.id === turn.conversation.id ? current.messages : []),
          turn.user_message,
          turn.assistant_message,
        ],
      }));
      setPendingQuestion(null);
      setPreview(EMPTY_PREVIEW);
      setStreamStatus("");
      await refreshHistory();
    } catch (requestError) {
      setError(requestError instanceof Error ? requestError.message : "流式问答失败");
      setQuestion(cleanQuestion);
      setPendingQuestion(null);
      setPreview(EMPTY_PREVIEW);
      setStreamStatus("");
    } finally {
      setSending(false);
    }
  }

  const readyDocuments = documents.filter((item) => item.status === "ready");
  const activeScope = active
    ? active.document_ids.length === 0
      ? "全部已就绪文献"
      : active.document_ids
          .map((id) => {
            const document = documents.find((item) => item.id === id);
            return document?.title || document?.original_filename || id;
          })
          .join("、")
    : null;

  return (
    <div className="chat-workbench">
      <aside className="chat-history">
        <div className="chat-history-heading">
          <div>
            <strong>会话历史</strong>
            <small>{conversations.length} 个会话</small>
          </div>
          <button disabled={sending} onClick={startConversation} type="button">＋ 新对话</button>
        </div>
        <div className="chat-history-list">
          {loading && <p className="chat-empty">正在读取历史…</p>}
          {!loading && conversations.length === 0 && (
            <p className="chat-empty">尚无历史会话，向文献提出第一个问题吧。</p>
          )}
          {conversations.map((conversation) => (
            <div
              className={`chat-history-item ${active?.id === conversation.id ? "active" : ""}`}
              key={conversation.id}
            >
              <button onClick={() => void openConversation(conversation.id)} type="button">
                <strong>{conversation.title}</strong>
                <span>{conversation.message_count} 条消息</span>
              </button>
              <button
                aria-label={`删除会话 ${conversation.title}`}
                className="chat-delete"
                disabled={sending}
                onClick={() => void removeConversation(conversation)}
                type="button"
              >
                ×
              </button>
            </div>
          ))}
        </div>
      </aside>

      <section className="chat-main">
        <header className="chat-main-heading">
          <div>
            <strong>{active?.title || "新的科研问答"}</strong>
            <small>{activeScope || "发送首条问题时固定检索范围"}</small>
          </div>
          <span>{agentLabel}</span>
        </header>

        <div className="chat-transcript" ref={transcriptRef}>
          {!active?.messages.length && !pendingQuestion && (
            <div className="chat-welcome">
              <strong>基于原文证据进行连续追问</strong>
              <p>系统会保留最近对话帮助理解“它”“第二个”等指代，但每一轮事实仍会重新检索 PDF 并生成独立引用。</p>
            </div>
          )}
          {active?.messages.map((message) => (
            <ChatMessage
              key={message.id}
              message={message}
              onOpenEvidence={onOpenEvidence}
            />
          ))}
          {pendingQuestion && (
            <ChatMessage
              message={{
                id: "pending-user",
                conversation_id: active?.id || "pending",
                sequence: 0,
                role: "user",
                content: pendingQuestion,
                claims: [],
                evidence: [],
                trace: [],
                insufficient_evidence: false,
                provider: null,
                model: null,
                created_at: new Date().toISOString(),
              }}
              onOpenEvidence={onOpenEvidence}
            />
          )}
          {sending && (
            <article className="chat-message assistant streaming">
              <div className="chat-avatar">AI</div>
              <div className="chat-bubble">
                <small>{preview.model || agentLabel} · {streamStatus || "正在处理…"}</small>
                <p>{preview.content || "正在查找可验证证据…"}<span className="stream-cursor" /></p>
                <ClaimList
                  claims={preview.claims}
                  evidence={preview.evidence}
                  onOpenEvidence={onOpenEvidence}
                />
              </div>
            </article>
          )}
        </div>

        {error && <div className="chat-error">{error}</div>}
        <form className="chat-composer" onSubmit={submitQuestion}>
          {!active && (
            <label>
              新会话检索范围
              <select
                value={selectedDocument}
                onChange={(event) => setSelectedDocument(event.target.value)}
              >
                <option value="all">全部已就绪文献</option>
                {readyDocuments.map((document) => (
                  <option key={document.id} value={document.id}>
                    {document.title || document.original_filename}
                  </option>
                ))}
              </select>
            </label>
          )}
          <div>
            <textarea
              disabled={sending}
              onChange={(event) => setQuestion(event.target.value)}
              placeholder={active ? "继续追问，例如：第二个参数为什么这样设置？" : "例如：这篇论文使用了哪些训练超参数？"}
              rows={3}
              value={question}
            />
            <button disabled={sending || !question.trim()} type="submit">
              {sending ? "回答生成中…" : "发送问题"}
            </button>
          </div>
          <small>对话上下文不作为证据；所有事实必须引用本轮检索到的 PDF 原文。</small>
        </form>
      </section>
    </div>
  );
}

function ChatMessage({
  message,
  onOpenEvidence,
}: {
  message: ConversationMessage;
  onOpenEvidence: (evidence: EvidenceAnchor) => void;
}) {
  return (
    <article className={`chat-message ${message.role}`}>
      <div className="chat-avatar">{message.role === "user" ? "你" : "AI"}</div>
      <div className="chat-bubble">
        {message.role === "assistant" && (
          <small>
            {message.model || message.provider || "Agent"} · {message.insufficient_evidence ? "证据不足" : "证据约束回答"}
          </small>
        )}
        <p>{message.content}</p>
        {message.role === "assistant" && (
          <>
            <ClaimList
              claims={message.claims}
              evidence={message.evidence}
              onOpenEvidence={onOpenEvidence}
            />
            {message.evidence.length > 0 && (
              <details className="chat-evidence-details">
                <summary>查看 {message.evidence.length} 条原文证据</summary>
                <div>
                  {message.evidence.map((evidence) => (
                    <button
                      key={evidence.evidence_id}
                      onClick={() => onOpenEvidence(evidence)}
                      type="button"
                    >
                      <strong>{evidence.evidence_id} · p.{evidence.page_number}</strong>
                      <span>{evidence.section || evidence.document_title}</span>
                      <small>{evidence.quote}</small>
                    </button>
                  ))}
                </div>
              </details>
            )}
            {message.trace.length > 0 && (
              <details className="chat-trace">
                <summary>执行轨迹</summary>
                {message.trace.map((step, index) => (
                  <div key={`${step.skill}-${index}`}>
                    <code>{step.skill}</code>
                    <span>{step.summary}</span>
                    <small>{step.duration_ms} ms</small>
                  </div>
                ))}
              </details>
            )}
          </>
        )}
      </div>
    </article>
  );
}

function ClaimList({
  claims,
  evidence,
  onOpenEvidence,
}: {
  claims: ConversationMessage["claims"];
  evidence: EvidenceAnchor[];
  onOpenEvidence: (evidence: EvidenceAnchor) => void;
}) {
  if (claims.length === 0) return null;
  const evidenceById = new Map(evidence.map((item) => [item.evidence_id, item]));
  return (
    <div className="chat-claims">
      <strong>逐条声明与引用</strong>
      {claims.map((claim, index) => (
        <div key={`${claim.text}-${index}`}>
          <p>{claim.text}</p>
          <span>
            {claim.evidence_ids.map((evidenceId) => {
              const anchor = evidenceById.get(evidenceId);
              return (
                <button
                  disabled={!anchor}
                  key={evidenceId}
                  onClick={() => anchor && onOpenEvidence(anchor)}
                  title={anchor?.quote}
                  type="button"
                >
                  {evidenceId}{anchor ? ` · p.${anchor.page_number}` : ""}
                </button>
              );
            })}
          </span>
        </div>
      ))}
    </div>
  );
}
