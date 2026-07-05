/**
 * src/types/challenge.ts
 *
 * TypeScript types for the Challenge Room ONLY.
 * The existing src/types.ts (ClassicRoom) is NOT modified.
 *
 * Import these wherever ChallengeRoom.tsx needs types:
 *   import { UserRank, ChallengeQuestion, ... } from '../types/challenge';
 *
 * Then in ChallengeRoom.tsx remove the imports from '../types' that
 * belong to challenge (UserRank, ChallengeLevel, ChallengeQuestion,
 * ChallengeSessionState, Rank) and re-import from here.
 */

// ── Rank system ──────────────────────────────────────────────────────────

/** Rank letters from weakest to strongest */
export type Rank = 'E' | 'D' | 'C' | 'B' | 'A';

/** Challenge levels 1-5, same as difficulty 1-5 */
export type ChallengeLevel = 1 | 2 | 3 | 4 | 5;

/** Topic type — kept identical to ClassicRoom to reuse backend */
export type TopicType = 'History' | 'Geography' | 'Mixed';


// ── API response shapes (match backend Pydantic models exactly) ───────────

/**
 * GET /api/challenge/user/{user_id}/rank
 * Backend: UserRankOut
 */
export interface UserRank {
  current_rank    : Rank;
  rank_points     : number;
  available_levels: ChallengeLevel[];
  total_sessions  : number;
  total_questions : number;

  /** Convenience alias used by the frontend level-card display */
  level_access    : ChallengeLevel[];   // same as available_levels
  total_points    : number;             // alias for rank_points
}

/**
 * POST /api/challenge/start-session
 * Backend: StartSessionOut
 */
export interface StartSessionResponse {
  session_id      : string;
  current_level   : ChallengeLevel;
  rank_points     : number;
  available_levels: ChallengeLevel[];
  current_rank    : Rank;
  topic           : TopicType;
}

/**
 * POST /api/challenge/generate-question
 * Backend: ChallengeQuestionOut
 */
export interface ChallengeQuestion {
  id           : string;
  text         : string;
  options      : string[];
  explanation  : string;
  level        : ChallengeLevel;
  points_value : number;    // points for a correct answer at this level
  is_free_text : boolean;
}

/**
 * Forced level change signal returned from submit-answer.
 * Backend: ForceLevelChange
 */
export interface ForceLevelChange {
  direction: 'up' | 'down';
  reason   : string;
}

/**
 * POST /api/challenge/submit-answer
 * Backend: SubmitChallengeAnswerOut
 */
export interface SubmitAnswerResponse {
  id                : string | null;
  is_correct        : boolean;
  correct_answer    : string;
  explanation       : string;
  points_change     : number;       // signed: +7 or -4
  new_rank_points   : number;       // session running total
  new_level         : ChallengeLevel;
  streak_correct    : number;
  streak_wrong      : number;
  force_level_change: ForceLevelChange | null;
}

/**
 * POST /api/challenge/session/{id}/end
 * Backend: EndSessionOut
 */
export interface EndSessionResponse {
  session_id         : string;
  total_questions    : number;
  correct_answers    : number;
  total_points_earned: number;
  new_rank           : Rank;
  new_rank_points    : number;
  rank_changed       : boolean;
}


// ── Frontend session state (held in React useState) ──────────────────────

/**
 * Full in-memory session state managed by ChallengeRoom.tsx.
 * Not sent to the backend — it mirrors the backend DB row.
 */
export interface ChallengeSessionState {
  session_id    : string;
  topic         : TopicType;
  current_level : ChallengeLevel;
  current_rank  : Rank;
  rank_points   : number;             // session-level points
  streak_correct: number;
  streak_wrong  : number;
  questions     : ChallengeQuestion[];
  currentIndex  : number;
  score         : number;             // correct answer count
  pointsEarned  : number;             // alias for rank_points (used by UI)
  force_level_change: ForceLevelChange | null;
}
