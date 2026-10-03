import { API_BASE_URL, getAccessToken } from "@/lib/api";

/**
 * Native EventSource can't send a custom `Authorization` header, and this
 * app has no other way to authenticate a request (no httpOnly session
 * cookie -- see api.ts). So streams are read manually via `fetch` +
 * `ReadableStream`, exactly like abyss_frontend's stream-service.ts: it
 * hits the same constraint for the same reason and solves it the same way.
 */

export interface SseFrame<T = unknown> {
  id: number;
  event: string;
  data: T;
}

function parseFrame(raw: string): SseFrame | null {
  if (!raw.trim() || raw.startsWith(":")) {
    return null; // heartbeat/comment line, or a blank keep-alive
  }
  let id: number | null = null;
  let event = "message";
  const dataLines: string[] = [];
  for (const line of raw.split("\n")) {
    if (line.startsWith("id:")) {
      id = Number(line.slice(3).trim());
    } else if (line.startsWith("event:")) {
      event = line.slice(6).trim();
    } else if (line.startsWith("data:")) {
      dataLines.push(line.slice(5).trim());
    }
  }
  if (id === null) return null;

  const dataText = dataLines.join("\n");
  let data: unknown = {};
  if (dataText) {
    try {
      data = JSON.parse(dataText);
    } catch {
      data = { content: dataText };
    }
  }
  return { id, event, data };
}

export interface OpenChatStreamOptions {
  signal: AbortSignal;
  lastEventId?: number;
  onFrame: (frame: SseFrame) => void;
}

/**
 * Opens the SSE stream for one chat turn and calls `onFrame` for every
 * event as it arrives, resolving once the stream closes (naturally, via
 * abort, or on error). Reconnecting mid-turn with `lastEventId` is safe:
 * the backend's in-memory buffer replays only chunks after that index
 * (see app/utils/stream_manager.py), so no frame is delivered twice.
 */
export async function openChatStream(
  streamId: string,
  { signal, lastEventId, onFrame }: OpenChatStreamOptions
): Promise<void> {
  const token = getAccessToken();
  const headers: Record<string, string> = { Accept: "text/event-stream" };
  if (token) headers.Authorization = `Bearer ${token}`;
  if (lastEventId !== undefined) headers["Last-Event-ID"] = String(lastEventId);

  const response = await fetch(
    `${API_BASE_URL}/api/v1/chat/streams/${streamId}`,
    { headers, signal }
  );

  if (!response.ok || !response.body) {
    throw new Error(`Failed to open chat stream (status ${response.status})`);
  }

  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";

  try {
    while (true) {
      const { value, done } = await reader.read();
      if (done) break;
      buffer += decoder.decode(value, { stream: true });

      let boundary = buffer.indexOf("\n\n");
      while (boundary !== -1) {
        const rawFrame = buffer.slice(0, boundary);
        buffer = buffer.slice(boundary + 2);
        const frame = parseFrame(rawFrame);
        if (frame) onFrame(frame);
        boundary = buffer.indexOf("\n\n");
      }
    }
  } finally {
    reader.releaseLock();
  }
}
