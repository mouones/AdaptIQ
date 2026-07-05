/** Shared TypeScript types for visual payloads. */

import type { TopicType } from '../types';

export type VisualQuestionType = 'M' | 'T';

export interface VisualQuestion {
  id: string;
  image_url: string;
  text: string;
  options: string[];
  topic: TopicType;
  level: number;
  question_type: VisualQuestionType;
  options_count: number;
  shape_path: string | null;
  shape_view_box: string | null;
  show_flag: boolean;
  show_shape: boolean;
}

export interface StartVisualSessionResponse {
  session_id: string;
  topic: TopicType;
  level: number;
  total_questions: number;
}

export interface SubmitVisualAnswerResponse {
  is_correct: boolean;
  correct_answer: string;
  explanation: string;
  next_question: VisualQuestion | null;
  current_level: number;
}

export interface VisualEndSessionResponse {
  session_id: string;
  topic: TopicType;
  level: number;
  score: number;
  questions_seen: number;
  total_questions: number;
  accuracy_percent: number;
}
