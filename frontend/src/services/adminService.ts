/**
 * src/services/adminService.ts
 *
 * Thin admin API client for dashboard cards and concept analytics.
 */

import { API_BASE } from '../config';
import { authFetch, responseErrorMessage } from './http';

export interface AdminOverview {
  users: { total: number; active: number; admin?: number; banned?: number; latest_created_at: string | null };
  questions: {
    total: number;
    llm_generated?: number;
    generated?: number;
    seeded?: number;
    admin?: number;
    unknown?: number;
    by_category?: Record<string, number>;
    by_source?: Record<string, number>;
    cached?: number;
    latest_created_at: string | null;
  };
  sessions: { classic?: number; challenge: number; custom: number; pvp?: number };
  concepts: { total: number; mastery_rows: number };
  responses: { total: number };
  pvp?: { total_matches: number; rated_players: number };
}

export interface AdminConceptStat {
  concept_id: string;
  name: string;
  topic: string;
  scope?: string;
  tracked_users: number;
  avg_theta: number;
}

export interface AdminCustomTopicCandidate {
  type: string;
  name: string;
  slug: string;
  description: string;
  source_topic: string;
  available_question_count: number;
  approved: boolean;
  is_active?: boolean;
  total_facts_count?: number;
  candidate_source: 'catalogue' | 'question_bank' | string;
}

export interface AdminCustomTopicApprovalRequest {
  type: string;
  name: string;
  slug?: string;
  description?: string;
  source_topic?: string;
  max_facts?: number;
  context?: string;
}

export interface AdminCustomTopicApprovalResult {
  success: boolean;
  created_topic: boolean;
  slug: string;
  type: string;
  name: string;
  topic: string;
  facts_created: number;
  total_facts: number;
}

// Fetch high-level admin overview counters.
export async function fetchAdminOverview(): Promise<AdminOverview> {
  const res = await authFetch(`${API_BASE}/api/admin/overview`);
  if (!res.ok) {
    throw new Error(await responseErrorMessage(res));
  }
  const data = await res.json().catch(() => ({}));
  return data as AdminOverview;
}

// Fetch top concepts ordered by tracked-user activity.
export async function fetchTopConcepts(limit = 10): Promise<AdminConceptStat[]> {
  const res = await authFetch(`${API_BASE}/api/admin/top-concepts?limit=${limit}`);
  if (!res.ok) {
    throw new Error(await responseErrorMessage(res));
  }
  const data = await res.json().catch(() => ({}));
  return (data.items ?? []) as AdminConceptStat[];
}

// Fetch Custom Room topic candidates that can be approved by an admin.
export async function fetchCustomTopicCandidates(limit = 50): Promise<AdminCustomTopicCandidate[]> {
  const safeLimit = Math.max(1, Math.min(200, Math.round(limit)));
  const res = await authFetch(`${API_BASE}/api/admin/custom-topics/candidates?limit=${safeLimit}`);
  if (!res.ok) {
    throw new Error(await responseErrorMessage(res));
  }
  const data = await res.json().catch(() => ({}));
  return (data.items ?? []) as AdminCustomTopicCandidate[];
}

// Approve a Custom Room topic and seed facts from eligible question-bank rows.
export async function approveCustomTopic(
  payload: AdminCustomTopicApprovalRequest,
): Promise<AdminCustomTopicApprovalResult> {
  const res = await authFetch(`${API_BASE}/api/admin/custom-topics/approve`, {
    method: 'POST',
    body: JSON.stringify(payload),
  });
  if (!res.ok) {
    throw new Error(await responseErrorMessage(res));
  }
  return res.json() as Promise<AdminCustomTopicApprovalResult>;
}

// Toggle the active state of a Custom Room topic (approved custom or built-in).
export async function toggleCustomTopicActive(
  slug: string,
  isActive: boolean,
): Promise<{ slug: string; is_active: boolean }> {
  const res = await authFetch(`${API_BASE}/api/admin/custom-topics/toggle-active`, {
    method: 'POST',
    body: JSON.stringify({ slug, is_active: isActive }),
  });
  if (!res.ok) {
    throw new Error(await responseErrorMessage(res));
  }
  return res.json() as Promise<{ slug: string; is_active: boolean }>;
}

// Bulk approve governance for all questions matching a specific topic/sub-topic.
export async function massApproveTopicQuestions(
  topic: string,
  subTopic?: string,
): Promise<{ success: boolean; count: number }> {
  const res = await authFetch(`${API_BASE}/api/admin/governance/mass-approve`, {
    method: 'POST',
    body: JSON.stringify({ topic, sub_topic: subTopic || undefined }),
  });
  if (!res.ok) {
    throw new Error(await responseErrorMessage(res));
  }
  return res.json() as Promise<{ success: boolean; count: number }>;
}
