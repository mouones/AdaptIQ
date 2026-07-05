/**
 * services/pvpService.ts — API calls for PvP Room.
 *
 * Covers:
 *   - joinQueue / leaveQueue / getQueueStatus
 *   - getMatch / getMatchState / submitAnswer / endMatch / forfeitMatch
 *   - getRating / getLeaderboard
 */

import { API_BASE } from '../config';
import { authFetch, getSessionUserId, responseErrorMessage } from './http';
import { notifyDashboardStatsUpdated } from './dashboardEvents';

// ── Types ─────────────────────────────────────────────────────────────────

export interface JoinQueueResponse {
  queue_id: string;
  status: string;
  message: string;
}

export interface LeaveQueueResponse {
  success: boolean;
  message?: string;
}

export interface QueueStatusResponse {
  status: 'waiting' | 'matched' | 'not_in_queue' | 'expired';
  match_id: string | null;
  opponent_username: string | null;
  topic: string | null;
  message: string;
}

export interface PvPQuestion {
  id: string;
  text: string;
  options: string[];
  index: number;
}

export interface PvPMatchData {
  match_id: string;
  user1_id: string;
  user2_id: string;
  topic: string;
  status: string;
  total_questions: number;
  questions: PvPQuestion[];
  user1_score: number;
  user2_score: number;
  user1_finished: boolean;
  user2_finished: boolean;
}

export interface PvPMatchState {
  match_id: string;
  status: string;
  user1_score: number;
  user2_score: number;
  user1_finished: boolean;
  user2_finished: boolean;
  winner_id: string | null;
}

export interface SubmitAnswerResponse {
  is_correct: boolean;
  correct_answer: string;
  explanation: string;
  your_score: number;
  opponent_score: number;
  questions_answered: number;
  match_finished: boolean;
  next_question?: PvPQuestion | null;
}

export interface EndMatchResponse {
  match_id: string;
  winner_id: string | null;
  result: 'win' | 'loss' | 'draw';
  your_score: number;
  opponent_score: number;
  elo_change: number;
  new_elo: number;
  opponent_username: string;
}

export interface PvPRating {
  user_id: string;
  elo_rating: number;
  total_matches: number;
  total_wins: number;
  total_losses: number;
  total_draws: number;
  win_streak: number;
  best_streak: number;
  win_rate: number;
}

export interface LeaderboardEntry {
  rank: number;
  user_id: string;
  username: string;
  elo_rating: number;
  total_wins: number;
  total_matches: number;
  win_rate: number;
}

// ── Helper ────────────────────────────────────────────────────────────────

const getUserId = (): string => {
  return getSessionUserId();
};

async function pvpFetch<T>(url: string, options: RequestInit = {}): Promise<T> {
  const res = await authFetch(`${API_BASE}${url}`, {
    ...options,
  });

  if (!res.ok) {
    throw new Error(await responseErrorMessage(res));
  }

  return res.json();
}

// ── Matchmaking ──────────────────────────────────────────────────────────

export const joinQueue = (topic: string): Promise<JoinQueueResponse> =>
  pvpFetch('/api/pvp/join-queue', {
    method: 'POST',
    body: JSON.stringify({
      user_id: getUserId(),
      topic,
    }),
  });

export const leaveQueue = (): Promise<LeaveQueueResponse> =>
  pvpFetch('/api/pvp/leave-queue', {
    method: 'DELETE',
    body: JSON.stringify({
      user_id: getUserId(),
    }),
  });

export const getQueueStatus = (): Promise<QueueStatusResponse> =>
  pvpFetch(`/api/pvp/queue-status?user_id=${getUserId()}`);

// ── Match ────────────────────────────────────────────────────────────────

export const getMatch = (matchId: string): Promise<PvPMatchData> =>
  pvpFetch(`/api/pvp/match/${matchId}`);

export const getPvPMatchState = (matchId: string): Promise<PvPMatchState> =>
  pvpFetch(`/api/pvp/match/${matchId}/state`);

export const submitPvPAnswer = (
  matchId: string,
  questionId: string,
  questionIndex: number,
  answer: string,
  timeTaken?: number,
): Promise<SubmitAnswerResponse> =>
  pvpFetch<SubmitAnswerResponse>(`/api/pvp/match/${matchId}/answer`, {
    method: 'POST',
    body: JSON.stringify({
      user_id: getUserId(),
      question_id: questionId,
      question_index: questionIndex,
      answer,
      time_taken: timeTaken,
    }),
  }).then((data) => {
    notifyDashboardStatsUpdated();
    return data;
  });

export const endPvPMatch = (matchId: string): Promise<EndMatchResponse> =>
  pvpFetch<EndMatchResponse>(`/api/pvp/match/${matchId}/end`, {
    method: 'POST',
  }).then((data) => {
    notifyDashboardStatsUpdated();
    return data;
  });

export const forfeitPvPMatch = (matchId: string): Promise<EndMatchResponse> =>
  pvpFetch<EndMatchResponse>(`/api/pvp/match/${matchId}/forfeit`, {
    method: 'POST',
  }).then((data) => {
    notifyDashboardStatsUpdated();
    return data;
  });

// ── Rating / Leaderboard ─────────────────────────────────────────────────

export const getPvPRating = (userId?: string): Promise<PvPRating> =>
  pvpFetch(`/api/pvp/user/${userId || getUserId()}/rating`);

export const getLeaderboard = (
  limit = 20,
): Promise<{ entries: LeaderboardEntry[]; total_players: number }> =>
  pvpFetch(`/api/pvp/leaderboard?limit=${limit}`);
