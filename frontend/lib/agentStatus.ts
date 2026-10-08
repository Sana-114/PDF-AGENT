// Configuration is not proof that a remote model accepted a generation request.
export function agentConfigurationLabel(status: {
  llm_configured: boolean;
  model: string | null;
  provider: string;
}): string {
  if (!status.llm_configured) return "LLM 未配置";
  if (status.provider === "extractive") return "原文整理模式";
  return status.model ? `${status.model} 已配置` : "LLM 已配置";
}
