/** Shared TypeScript types for chat payloads. */

export interface ChatMessage {
  id: string;
  sender: 'user' | 'assistant';
  text: string;
  timestamp: string;
  sources?: string[];
  topic?: 'history' | 'geography' | 'mixed';
  isError?: boolean;
}

export interface ChatResponse {
  answer: string;
  sources: string[];
  topic: 'history' | 'geography' | 'mixed';
  grounded: boolean;
}
