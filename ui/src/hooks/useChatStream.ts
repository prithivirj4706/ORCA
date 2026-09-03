import { useState } from 'react';
import { API_BASE } from '../api/client';
import type { ORCAResponse } from '../types/api';

export interface ChatStreamState {
  isStreaming: boolean;
  threadId?: string;
  trace: any[];
  result?: ORCAResponse;
  error?: string;
}

export function useChatStream() {
  const [state, setState] = useState<ChatStreamState>({ isStreaming: false, trace: [] });

  const ask = async (query: string, thread_id?: string) => {
    setState({ isStreaming: true, trace: [], result: undefined, error: undefined, threadId: thread_id });
    const body = JSON.stringify(thread_id ? { query, thread_id } : { query });

    try {
      const res = await fetch(`${API_BASE}/chat/stream`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body
      });

      if (!res.body) throw new Error("No readable stream");

      const reader = res.body.getReader();
      const dec = new TextDecoder();
      let buf = '';

      const handle = (chunk: string) => {
        const evMatch = /event: (\w+)/.exec(chunk);
        const dtMatch = /data: ([\s\S]*)$/.exec(chunk);
        
        if (!evMatch || !dtMatch) return;
        const ev = evMatch[1];
        
        let payload;
        try {
          payload = JSON.parse(dtMatch[1]);
        } catch (_) {
          return;
        }

        if (ev === 'start') {
          setState(s => ({ ...s, threadId: payload.thread_id }));
        } else if (ev === 'node') {
          setState(s => ({ ...s, trace: [...s.trace, payload] }));
        } else if (ev === 'result') {
          setState(s => ({ ...s, result: payload }));
        } else if (ev === 'error') {
          setState(s => ({ ...s, error: payload.error }));
        }
      };

      const pump = async (): Promise<void> => {
        const { done, value } = await reader.read();
        if (done) {
          if (buf.trim()) handle(buf);
          setState(s => ({ ...s, isStreaming: false }));
          return;
        }
        buf += dec.decode(value, { stream: true });
        const parts = buf.split('\n\n');
        buf = parts.pop() || '';
        parts.forEach(handle);
        return pump();
      };

      await pump();
    } catch (e: any) {
      setState(s => ({ ...s, isStreaming: false, error: e.message || 'Request failed' }));
    }
  };

  return { state, ask };
}
