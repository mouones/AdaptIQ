// ─── Custom Room Types ────────────────────────────────────────────────────────

export type TopicType =
  | 'History - World War I'
  | 'History - World War II'
  | 'History - Cold War'
  | 'History - Ancient Rome'
  | 'History - French Revolution'
  | 'History - Industrial Revolution'
  | 'Geography - United States'
  | 'Geography - Brazil'
  | 'Geography - France'
  | 'Geography - Egypt'
  | 'Geography - China'
  | 'Geography - Australia'
  | 'Geography - United Kingdom'
  | 'Geography - India'
  | 'Geography - Japan'
  | 'Geography - South Africa';

export interface HistoryTheme {
  id: string;
  name: string;
  slug: string;
  description: string;
}

// ─── API Request / Response shapes ───────────────────────────────────────────

export interface StartSessionRequest {
  user_id: string;  // UUID string
  topic: string;
  concept_id?: string;
}

export interface StartSessionResponse {
  session_id: string;
  topic: string;
  concept_id?: string;
  progress_percentage: number;
  total_questions_estimate: number;
}

export type CustomLevel = 1 | 2 | 3 | 4 | 5;

export interface GenerateQuestionRequest {
  session_id: string;
  topic: string;
  concept_id?: string;
  level?: CustomLevel;
}

export interface CustomQuestion {
  id: string;
  text: string;
  options: string[];          // empty array for is_free_text=true
  explanation: string;
  fact_id?: string;
  concept_id?: string;
  level: CustomLevel;
  is_free_text: boolean;
}

export interface SubmitAnswerRequest {
  session_id: string;
  question_id: string;
  answer: string;
  used_hint?: boolean;
  time_taken?: number;
}

export interface SubmitAnswerResponse {
  is_correct: boolean;
  correct_answer: string;
  explanation: string;
  new_progress_percentage: number;
  total_questions_this_session: number;
}

export interface EndSessionResponse {
  session_id: string;
  topic: string;
  questions_answered: number;
  correct_count: number;
  completion_percentage_after: number;
}

// ─── Frontend session state ───────────────────────────────────────────────────

export interface CustomSessionState {
  session_id: string;
  topic: string;
  concept_id?: string;
  concept_name?: string;
  questions: CustomQuestion[];
  currentIndex: number;
  score: number;
  pointsEarned: number;
  hintsUsed: number;
  startTime: number;
  isFinished: boolean;
  progress_percentage: number;
  currentLevel: CustomLevel;
  streakCorrect: number;
  streakWrong: number;
}
