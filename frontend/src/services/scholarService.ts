/** Frontend service helpers for scholarService behavior. */

import { API_BASE } from '../config';
import { authFetch } from './http';
import { ChatResponse } from '../types/chat';

export const askScholar = async (question: string, topicHint?: string): Promise<ChatResponse> => {
  const controller = new AbortController();
  const id = setTimeout(() => controller.abort(), 15000); // 15-second timeout

  try {
    const response = await authFetch(`${API_BASE}/api/chat/ask`, {
      method: "POST",
      body: JSON.stringify({
        question,
        topic_hint: topicHint,
      }),
      signal: controller.signal,
    });

    clearTimeout(id);

    if (response.status === 400) {
      const errorData = await response.json().catch(() => ({}));
      if (errorData.detail === "OUT_OF_SCOPE" || errorData.message === "OUT_OF_SCOPE") {
        throw new Error("OUT_OF_SCOPE");
      }
      throw new Error("BAD_REQUEST");
    }

    if (response.status === 503) {
      throw new Error("SERVICE_UNAVAILABLE");
    }

    if (!response.ok) {
      throw new Error("SERVER_ERROR");
    }

    const data: ChatResponse = await response.json();
    return data;

  } catch (error: any) {
    clearTimeout(id);

    // If aborted due to timeout
    if (error.name === 'AbortError') {
      throw new Error("TIMEOUT");
    }

    // If it's the specific OUT_OF_SCOPE or SERVICE_UNAVAILABLE errors, rethrow them
    if (error.message === "OUT_OF_SCOPE" || error.message === "SERVICE_UNAVAILABLE" || error.message === "BAD_REQUEST") {
      throw error;
    }

    console.warn("Scholar backend call failed.", error);
    throw new Error("SERVICE_UNAVAILABLE");
  }
};
