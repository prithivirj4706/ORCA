import { useEffect, useRef, useState } from 'react';
import { useChatStream } from '../hooks/useChatStream';
import { Answer, askHintFor } from './Answer';
import type { ORCAResponse } from '../types/api';

const SUGGESTIONS = [
  'Is it good for fishing near Kochi tomorrow morning?',
  'Where is the nearest PFZ today?',
  'Safest route from Kochi to Chennai',
  'Am I inside the Indian EEZ?',
  'കൊച്ചിയിൽ നാളെ രാവിലെ മീൻപിടിക്കാൻ നല്ലതാണോ?'
];

interface Props {
  onResult: (r: ORCAResponse) => void;
  onTrace: (trace: any[], live: boolean) => void;
  onEvidenceClick: (provenanceId: string) => void;
}

export function Conversation({ onResult, onTrace, onEvidenceClick }: Props) {
  const [input, setInput] = useState('');
  const [asked, setAsked] = useState(false);
  const inputRef = useRef<HTMLTextAreaElement>(null);
  const { state, ask } = useChatStream();

  const submit = (text: string) => {
    if (!text.trim() || state.isStreaming) return;
    setAsked(true);
    setInput('');
    void ask(text, state.threadId);
  };

  // Trace is lifted so it can live in its own panel and PERSIST after the run;
  // the previous version rendered it only while streaming, so it vanished the
  // moment it became useful to read.
  useEffect(() => {
    onTrace(state.trace, state.isStreaming);
  }, [state.trace, state.isStreaming, onTrace]);

  useEffect(() => {
    if (state.result) onResult(state.result);
  }, [state.result, onResult]);

  // When ORCA asks a question, put the cursor where the answer goes (F-57).
  //
  // This must wait for the stream to END. The `result` event arrives while
  // `isStreaming` is still true, and the textarea is disabled for exactly that
  // long -- so focusing on the result alone called focus() on a disabled
  // element, which does nothing, and nothing focused it afterwards. The
  // question was visible and the cursor was somewhere else.
  useEffect(() => {
    if (!state.isStreaming && state.result?.clarification_needed) {
      inputRef.current?.focus();
    }
  }, [state.isStreaming, state.result]);

  const placeholder = state.result?.clarification_needed
    ? askHintFor(state.result.clarification_needed)
    : asked ? 'Ask a follow-up…' : 'Is it good for fishing near Kochi tomorrow morning?';

  return (
    <div className="conversation-pane">
      <div className="pbody" id="answer-scroll">
        {state.isStreaming && !state.result && (
          <div className="empty"><span className="spin" /> Planning…</div>
        )}
        {state.error && <div className="error-box">{state.error}</div>}
        {state.result && (
          <Answer data={state.result} onEvidenceClick={onEvidenceClick} />
        )}
        {!state.result && !state.isStreaming && !state.error && (
          <div className="empty">
            Ask about safety, fishing conditions, maritime boundaries or a route.
            <br />
            <br />
            Answers are evidence-bound and every number traces to a source.
          </div>
        )}
      </div>

      <div className="ask">
        {!asked && (
          <div className="chips">
            {SUGGESTIONS.map((s) => (
              <div key={s} className="chip" title={s} onClick={() => submit(s)}>
                {s.length > 42 ? `${s.slice(0, 40)}…` : s}
              </div>
            ))}
          </div>
        )}
        <div className="askrow">
          <textarea
            ref={inputRef}
            value={input}
            rows={1}
            placeholder={placeholder}
            disabled={state.isStreaming}
            onChange={(e) => setInput(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === 'Enter' && !e.shiftKey) {
                e.preventDefault();
                submit(input);
              }
            }}
          />
          <button className="go" onClick={() => submit(input)}
                  disabled={state.isStreaming || !input.trim()}>
            →
          </button>
        </div>
      </div>
    </div>
  );
}
