export function readableRequestError(error: unknown, fallback: string): string {
  if (!(error instanceof Error) || !error.message.trim()) return fallback;
  if (/failed to fetch|networkerror|load failed/i.test(error.message)) {
    return "无法连接后端服务，请确认服务已启动。";
  }
  return error.message;
}
