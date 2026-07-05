/**
 * pages/AdminDashboard.tsx - Admin dashboard with overview stats, user/question/session lists.
 *
 * Features:
 *   - Overview cards (users, questions, sessions, PvP)
 *   - User list with search + toggle active/admin
 *   - Question list with topic filter
 *   - Session list (challenge + custom)
 *   - Concept mastery overview
 *   - Monitoring stats
 *
 * Requires admin privileges (is_admin = true).
 */

import { useState, useEffect } from 'react';
import { useNavigate } from 'react-router-dom';
import { useAuth } from '../context/AuthContext';
import InternalLayout from '../components/InternalLayout';
import { API_BASE } from '../config';
import { authFetch } from '../services/http';
import {
  approveCustomTopic,
  fetchCustomTopicCandidates,
  toggleCustomTopicActive,
  massApproveTopicQuestions,
  type AdminCustomTopicApprovalResult,
  type AdminCustomTopicCandidate,
} from '../services/adminService';

interface OverviewData {
  users: { total: number; active: number; admin?: number; banned?: number; latest_created_at: string | null };
  questions: {
    total: number;
    generated?: number;
    seeded?: number;
    unknown?: number;
    by_source?: Record<string, number>;
    latest_created_at: string | null;
  };
  sessions: { classic?: number; challenge: number; custom: number; pvp: number };
  concepts: { total: number; mastery_rows: number };
  responses: { total: number };
  pvp: { total_matches: number; rated_players: number };
}

interface DailyAnalyticsItem {
  date: string;
  new_users: number;
  responses: number;
  correct: number;
  classic_sessions: number;
  challenge_sessions: number;
  custom_sessions: number;
  pvp_matches: number;
}

interface TopUserAnalytics {
  user_id: string;
  display_name: string;
  username: string | null;
  email: string | null;
  points: number;
  responses: number;
  correct: number;
  accuracy: number;
}

interface DailyAnalyticsData {
  days: number;
  start_date: string | null;
  end_date: string | null;
  items: DailyAnalyticsItem[];
  totals: {
    new_users: number;
    responses: number;
    correct: number;
    classic_sessions: number;
    challenge_sessions: number;
    custom_sessions: number;
    pvp_matches: number;
  };
  top_users: TopUserAnalytics[];
}

interface InspectorColumn {
  name: string;
  type: string;
  nullable: boolean;
  primary_key: boolean;
}

interface InspectorTable {
  name: string;
  row_count: number;
  columns: InspectorColumn[];
}

// GET helper for admin dashboard endpoints.
async function adminFetch<T>(path: string): Promise<T> {
  const res = await authFetch(`${API_BASE}${path}`);
  if (!res.ok) {
    let message = `HTTP ${res.status}`;
    try {
      const payload = await res.json();
      if (typeof payload?.detail === 'string' && payload.detail.trim()) {
        message = payload.detail;
      }
    } catch {
      // Ignore body parsing errors and keep default status message.
    }
    throw new Error(message);
  }
  return res.json();
}

// Mutation helper for admin endpoints.
async function adminPost(path: string, body?: any): Promise<any> {
  const res = await authFetch(`${API_BASE}${path}`, {
    method: 'POST',
    body: body !== undefined ? JSON.stringify(body) : undefined,
  });
  if (!res.ok) {
    let message = `HTTP ${res.status}`;
    try {
      const payload = await res.json();
      if (typeof payload?.detail === 'string' && payload.detail.trim()) {
        message = payload.detail;
      }
    } catch {
      // Ignore body parsing errors and keep default status message.
    }
    throw new Error(message);
  }
  return res.json();
}

// PATCH helper for admin mutation endpoints.
async function adminPatch(path: string, body?: any): Promise<any> {
  const res = await authFetch(`${API_BASE}${path}`, {
    method: 'PATCH',
    body: body !== undefined ? JSON.stringify(body) : undefined,
  });
  if (!res.ok) {
    let message = `HTTP ${res.status}`;
    try {
      const payload = await res.json();
      if (typeof payload?.detail === 'string' && payload.detail.trim()) {
        message = payload.detail;
      }
    } catch {
      // Ignore body parsing errors and keep default status message.
    }
    throw new Error(message);
  }
  return res.json();
}

// DELETE helper for admin mutation endpoints.
async function adminDelete(path: string): Promise<any> {
  const res = await authFetch(`${API_BASE}${path}`, { method: 'DELETE' });
  if (!res.ok) {
    let message = `HTTP ${res.status}`;
    try {
      const payload = await res.json();
      if (typeof payload?.detail === 'string' && payload.detail.trim()) {
        message = payload.detail;
      }
    } catch {
      // Ignore body parsing errors and keep default status message.
    }
    throw new Error(message);
  }
  return res.json();
}

type Tab = 'overview' | 'users' | 'questions' | 'sessions' | 'topics' | 'governance' | 'inspector' | 'monitoring';

// Render admin dashboard tabs, tables, and refresh actions.
export default function AdminDashboard() {
  const { user } = useAuth();
  const navigate = useNavigate();
  const [tab, setTab] = useState<Tab>('overview');

  // Overview
  const [overview, setOverview] = useState<OverviewData | null>(null);
  const [topConcepts, setTopConcepts] = useState<any[]>([]);
  const [dailyAnalytics, setDailyAnalytics] = useState<DailyAnalyticsData | null>(null);
  const [overviewError, setOverviewError] = useState('');
  // Shared error surface for the simple list tabs (users/questions/sessions/
  // monitoring/governance). Only one tab is active at a time, so a single slot
  // is enough to make a 401/403/500 visible instead of a silently blank tab.
  const [tabError, setTabError] = useState('');

  // Users
  const [users, setUsers] = useState<any[]>([]);
  const [usersTotal, setUsersTotal] = useState(0);
  const [userSearch, setUserSearch] = useState('');
  const [userPage, setUserPage] = useState(1);
  const [selectedUserDetail, setSelectedUserDetail] = useState<any | null>(null);
  const [selectedUserLoading, setSelectedUserLoading] = useState(false);
  const [selectedUserError, setSelectedUserError] = useState('');
  const [selectedUserEditUsername, setSelectedUserEditUsername] = useState('');
  const [selectedUserEditEmail, setSelectedUserEditEmail] = useState('');
  const [selectedUserEditLevel, setSelectedUserEditLevel] = useState('');
  const [selectedUserEditPoints, setSelectedUserEditPoints] = useState('');
  const [selectedUserEditSaving, setSelectedUserEditSaving] = useState(false);
  const [selectedUserEditError, setSelectedUserEditError] = useState('');
  const [selectedUserEditSuccess, setSelectedUserEditSuccess] = useState('');
  const [banMinutesByUser, setBanMinutesByUser] = useState<Record<string, string>>({});
  const [banReasonByUser, setBanReasonByUser] = useState<Record<string, string>>({});

  // Questions
  const [questions, setQuestions] = useState<any[]>([]);
  const [qTotal, setQTotal] = useState(0);
  const [qTopic, setQTopic] = useState('');
  const [qPage, setQPage] = useState(1);
  const [newQuestionText, setNewQuestionText] = useState('');
  const [newQuestionOptions, setNewQuestionOptions] = useState('');
  const [newQuestionAnswer, setNewQuestionAnswer] = useState('');
  const [newQuestionExplanation, setNewQuestionExplanation] = useState('');
  const [newQuestionTopic, setNewQuestionTopic] = useState('history');

  // Sessions
  const [sessions, setSessions] = useState<any[]>([]);



  // Monitoring
  const [monitoring, setMonitoring] = useState<any>(null);

  // Governance
  const [govRules, setGovRules] = useState<any[]>([]);
  const [govAudits, setGovAudits] = useState<any[]>([]);
  const [govAuditsTotal, setGovAuditsTotal] = useState(0);
  const [govAcceptance, setGovAcceptance] = useState<any>(null);
  const [govAuditFilter, setGovAuditFilter] = useState<'all' | 'approved' | 'rejected'>('all');
  const [govNewKind, setGovNewKind] = useState('keyword');
  const [govNewPattern, setGovNewPattern] = useState('');

  // Custom topic approval
  const [customTopicCandidates, setCustomTopicCandidates] = useState<AdminCustomTopicCandidate[]>([]);
  const [customTopicLoading, setCustomTopicLoading] = useState(false);
  const [customTopicError, setCustomTopicError] = useState('');
  const [customTopicSuccess, setCustomTopicSuccess] = useState('');
  const [customTopicApprovingSlug, setCustomTopicApprovingSlug] = useState('');
  const [lastCustomTopicApproval, setLastCustomTopicApproval] = useState<AdminCustomTopicApprovalResult | null>(null);

  // Manual topic fields
  const [manualTopicType, setManualTopicType] = useState('History');
  const [manualTopicName, setManualTopicName] = useState('');
  const [manualTopicSource, setManualTopicSource] = useState('');
  const [manualTopicDesc, setManualTopicDesc] = useState('');
  const [manualTopicContext, setManualTopicContext] = useState('');
  const [manualTopicLoading, setManualTopicLoading] = useState(false);

  // Mass-approve topic questions
  const [massApproveTopic, setMassApproveTopic] = useState('History');
  const [massApproveSubTopic, setMassApproveSubTopic] = useState('');
  const [massApproveLoading, setMassApproveLoading] = useState(false);
  const [massApproveError, setMassApproveError] = useState('');
  const [massApproveSuccess, setMassApproveSuccess] = useState('');

  // Session drill-down details modal
  const [detailedSessionId, setDetailedSessionId] = useState<string | null>(null);
  const [detailedSessionType, setDetailedSessionType] = useState<string | null>(null);
  const [detailedSessionData, setDetailedSessionData] = useState<any>(null);
  const [detailedSessionLoading, setDetailedSessionLoading] = useState(false);
  const [detailedSessionError, setDetailedSessionError] = useState('');

  // DB Inspector
  const [inspectorTables, setInspectorTables] = useState<InspectorTable[]>([]);
  const [inspectorSelectedTable, setInspectorSelectedTable] = useState('');
  const [inspectorColumns, setInspectorColumns] = useState<InspectorColumn[]>([]);
  const [inspectorRows, setInspectorRows] = useState<Record<string, unknown>[]>([]);
  const [inspectorTotalRows, setInspectorTotalRows] = useState(0);
  const [inspectorLimit, setInspectorLimit] = useState(100);
  const [inspectorError, setInspectorError] = useState('');
  const [inspectorLoading, setInspectorLoading] = useState(false);

  useEffect(() => {
    if (!user?.is_admin) return;
    loadOverview();
  }, [user]);

  useEffect(() => {
    if (tab === 'users') loadUsers();
    if (tab === 'questions') loadQuestions();
    if (tab === 'sessions') loadSessions();
    if (tab === 'topics') loadCustomTopicCandidates();
    if (tab === 'monitoring') loadMonitoring();
    if (tab === 'governance') loadGovernance();
  }, [tab, userSearch, userPage, qTopic, qPage, govAuditFilter]);

  useEffect(() => {
    if (tab !== 'inspector') return;
    loadInspectorSchema();
  }, [tab]);

  useEffect(() => {
    if (tab !== 'inspector' || !inspectorSelectedTable) return;
    loadInspectorTable(inspectorSelectedTable);
  }, [tab, inspectorSelectedTable, inspectorLimit]);

  // Load top-level overview and top-concepts widgets.
  // Each widget loads independently so a single failing sub-request degrades to
  // an inline notice instead of blanking the whole Overview tab.
  const loadOverview = async () => {
    setOverviewError('');
    const [ovRes, tcRes, daRes] = await Promise.allSettled([
      adminFetch<OverviewData>('/api/admin/overview'),
      adminFetch<{ items: any[] }>('/api/admin/top-concepts'),
      adminFetch<DailyAnalyticsData>('/api/admin/analytics/daily?days=14'),
    ]);

    if (ovRes.status === 'fulfilled') setOverview(ovRes.value);
    if (tcRes.status === 'fulfilled') setTopConcepts(tcRes.value.items);
    if (daRes.status === 'fulfilled') setDailyAnalytics(daRes.value);

    const failed: string[] = [];
    if (ovRes.status === 'rejected') failed.push('overview');
    if (tcRes.status === 'rejected') failed.push('top concepts');
    if (daRes.status === 'rejected') failed.push('daily analytics');
    if (failed.length > 0) {
      const firstRejected = [ovRes, tcRes, daRes].find(
        (r): r is PromiseRejectedResult => r.status === 'rejected',
      );
      const reason = (firstRejected?.reason as any)?.message;
      setOverviewError(
        `Could not load: ${failed.join(', ')}.${reason ? ` (${reason})` : ''}`,
      );
    }
  };

  // Load paginated user list with optional search filter.
  const loadUsers = async () => {
    setTabError('');
    try {
      const params = new URLSearchParams({ page: String(userPage), per_page: '15' });
      if (userSearch) params.set('search', userSearch);
      const data = await adminFetch<any>(`/api/admin/users?${params}`);
      setUsers(data.items);
      setUsersTotal(data.total);
    } catch (err: any) {
      setTabError(err?.message ?? 'Failed to load users.');
    }
  };

  // Load one user's full admin profile payload (sessions, mastery, PvP stats).
  const syncSelectedUserEditForm = (payload: any) => {
    const userPayload = payload?.user || {};
    setSelectedUserEditUsername((userPayload.username || '').toString());
    setSelectedUserEditEmail((userPayload.email || '').toString());
    setSelectedUserEditLevel((userPayload.level || '').toString());
    setSelectedUserEditPoints(String(Number(userPayload.points ?? 0)));
  };

  const openUserDetail = async (userId: string) => {
    setSelectedUserLoading(true);
    setSelectedUserError('');
    setSelectedUserEditError('');
    setSelectedUserEditSuccess('');
    setSelectedUserDetail(null);
    try {
      const payload = await adminFetch<any>(`/api/admin/users/${userId}`);
      setSelectedUserDetail(payload);
      syncSelectedUserEditForm(payload);
    } catch (err: any) {
      setSelectedUserError(err?.message ?? 'Failed to load user profile details.');
    } finally {
      setSelectedUserLoading(false);
    }
  };

  const closeUserDetail = () => {
    setSelectedUserDetail(null);
    setSelectedUserError('');
    setSelectedUserLoading(false);
    setSelectedUserEditUsername('');
    setSelectedUserEditEmail('');
    setSelectedUserEditLevel('');
    setSelectedUserEditPoints('');
    setSelectedUserEditError('');
    setSelectedUserEditSuccess('');
    setSelectedUserEditSaving(false);
  };

  // Load paginated question inventory with optional topic filter.
  const loadQuestions = async () => {
    setTabError('');
    try {
      const params = new URLSearchParams({ page: String(qPage), per_page: '15' });
      if (qTopic) params.set('topic', qTopic);
      const data = await adminFetch<any>(`/api/admin/questions?${params}`);
      setQuestions(data.items);
      setQTotal(data.total);
    } catch (err: any) {
      setTabError(err?.message ?? 'Failed to load questions.');
    }
  };

  // Load recent sessions across room types.
  const loadSessions = async () => {
    setTabError('');
    try {
      const data = await adminFetch<any>('/api/admin/sessions');
      setSessions(data.items);
    } catch (err: any) {
      setTabError(err?.message ?? 'Failed to load sessions.');
    }
  };


  // Load monitoring metrics and recent operational diagnostics.
  const loadMonitoring = async () => {
    setTabError('');
    try {
      setMonitoring(await adminFetch('/api/admin/monitoring'));
    } catch (err: any) {
      setTabError(err?.message ?? 'Failed to load monitoring metrics.');
    }
  };

  // Load governance block rules and audit log.
  const loadGovernance = async () => {
    setTabError('');
    try {
      const [rulesData, auditsData] = await Promise.all([
        adminFetch<{ items: any[] }>('/api/admin/governance/blocked-rules'),
        adminFetch<{ items: any[]; total: number; persist_acceptance: any }>(
          `/api/admin/governance/audits?limit=50${govAuditFilter === 'approved' ? '&approved=true' : govAuditFilter === 'rejected' ? '&approved=false' : ''}`
        ),
      ]);
      setGovRules(rulesData.items || []);
      setGovAudits(auditsData.items || []);
      setGovAuditsTotal(auditsData.total || 0);
      setGovAcceptance(auditsData.persist_acceptance || null);
    } catch (err: any) {
      setTabError(err?.message ?? 'Failed to load governance data.');
    }
  };

  // Trigger mass-approval of questions for a topic/sub-topic.
  const handleMassApproveQuestions = async () => {
    setMassApproveError('');
    setMassApproveSuccess('');
    if (!massApproveTopic.trim()) {
      setMassApproveError('Topic name is required.');
      return;
    }
    setMassApproveLoading(true);
    try {
      const res = await massApproveTopicQuestions(massApproveTopic, massApproveSubTopic);
      setMassApproveSuccess(`Successfully approved ${res.count} questions for topic '${massApproveTopic}'${massApproveSubTopic ? ` (sub-topic: ${massApproveSubTopic})` : ''}.`);
      setMassApproveSubTopic('');
      await loadGovernance();
    } catch (err: any) {
      setMassApproveError(err?.message || 'Failed to complete mass-approval.');
    } finally {
      setMassApproveLoading(false);
    }
  };

  // Load Custom Room topic candidates that can be approved from the admin UI.
  const loadCustomTopicCandidates = async () => {
    setCustomTopicLoading(true);
    setCustomTopicError('');
    try {
      const items = await fetchCustomTopicCandidates(100);
      setCustomTopicCandidates(items);
    } catch (err: any) {
      setCustomTopicError(err?.message || 'Failed to load custom topic candidates.');
    } finally {
      setCustomTopicLoading(false);
    }
  };

  // Approve one Custom Room topic and refresh candidate state.
  const approveTopicCandidate = async (candidate: AdminCustomTopicCandidate) => {
    setCustomTopicApprovingSlug(candidate.slug);
    setCustomTopicError('');
    setCustomTopicSuccess('');
    setLastCustomTopicApproval(null);
    try {
      const result = await approveCustomTopic({
        type: candidate.type,
        name: candidate.name,
        slug: candidate.slug,
        description: candidate.description,
        source_topic: candidate.source_topic,
        max_facts: 100,
      });
      setLastCustomTopicApproval(result);
      if (result.facts_created === 0) {
        setCustomTopicError(
          `Warning: 0 facts were harvested for '${result.topic}'. Make sure governance-approved, safe questions exist for this topic.`
        );
      } else {
        setCustomTopicSuccess(
          `${result.topic} approved successfully. ${result.facts_created} new facts created, ${result.total_facts} total facts available.`,
        );
      }
      await loadCustomTopicCandidates();
    } catch (err: any) {
      setCustomTopicError(err?.message || 'Failed to approve custom topic.');
    } finally {
      setCustomTopicApprovingSlug('');
    }
  };

  const handleToggleTopicActive = async (candidate: AdminCustomTopicCandidate, nextActive: boolean) => {
    setCustomTopicError('');
    setCustomTopicSuccess('');
    try {
      await toggleCustomTopicActive(candidate.slug, nextActive);
      setCustomTopicSuccess(`Topic '${candidate.name}' ${nextActive ? 'activated' : 'deactivated'} successfully.`);
      await loadCustomTopicCandidates();
    } catch (err: any) {
      setCustomTopicError(err?.message || 'Failed to toggle topic active status.');
    }
  };

  const handleCreateManualTopic = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!manualTopicName.trim()) return;

    setManualTopicLoading(true);
    setCustomTopicError('');
    setCustomTopicSuccess('');

    try {
      const result = await approveCustomTopic({
        type: manualTopicType,
        name: manualTopicName.trim(),
        description: manualTopicDesc.trim() || undefined,
        source_topic: manualTopicSource.trim() || undefined,
        context: manualTopicContext.trim() || undefined,
        max_facts: 100,
      });

      setCustomTopicSuccess(
        `Topic "${result.topic}" created successfully. Created ${result.facts_created} facts, total ${result.total_facts} available.`
      );
      setManualTopicName('');
      setManualTopicSource('');
      setManualTopicDesc('');
      setManualTopicContext('');
      await loadCustomTopicCandidates();
    } catch (err: any) {
      setCustomTopicError(err?.message || 'Failed to create custom topic.');
    } finally {
      setManualTopicLoading(false);
    }
  };

  const loadSessionDetails = async (id: string, type: string) => {
    setDetailedSessionId(id);
    setDetailedSessionType(type);
    setDetailedSessionLoading(true);
    setDetailedSessionError('');
    setDetailedSessionData(null);

    try {
      const response = await authFetch(`${API_BASE}/api/admin/sessions/${type}/${id}`);
      if (!response.ok) {
        throw new Error('Failed to load session details');
      }
      const data = await response.json();
      setDetailedSessionData(data);
    } catch (err: any) {
      setDetailedSessionError(err?.message || 'Failed to load details.');
    } finally {
      setDetailedSessionLoading(false);
    }
  };

  // Create a new governance block rule.
  const createGovRule = async () => {
    if (!govNewPattern.trim()) return;
    try {
      const res = await authFetch(`${API_BASE}/api/admin/governance/blocked-rules`, {
        method: 'POST',
        body: JSON.stringify({ kind: govNewKind, pattern: govNewPattern.trim() }),
      });
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      setGovNewPattern('');
      loadGovernance();
    } catch { /* ignore */ }
  };

  // Toggle a governance block rule's active status.
  const toggleGovRule = async (ruleId: string, newActive: boolean) => {
    try {
      const res = await authFetch(`${API_BASE}/api/admin/governance/blocked-rules/${ruleId}`, {
        method: 'PATCH',
        body: JSON.stringify({ is_active: newActive }),
      });
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      loadGovernance();
    } catch { /* ignore */ }
  };

  // Delete a governance block rule.
  const deleteGovRule = async (ruleId: string) => {
    try {
      const res = await authFetch(`${API_BASE}/api/admin/governance/blocked-rules/${ruleId}`, {
        method: 'DELETE',
      });
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      loadGovernance();
    } catch { /* ignore */ }
  };

  // Load database schema for read-only table inspection.
  const loadInspectorSchema = async () => {
    setInspectorLoading(true);
    try {
      const data = await adminFetch<{ tables: InspectorTable[] }>('/api/admin/db/schema');
      const tables = Array.isArray(data.tables) ? data.tables : [];
      setInspectorTables(tables);
      setInspectorError('');
      if (!inspectorSelectedTable && tables.length > 0) {
        setInspectorSelectedTable(tables[0].name);
      }
    } catch (err: any) {
      setInspectorError(err?.message ?? 'Failed to load DB schema.');
    } finally {
      setInspectorLoading(false);
    }
  };

  // Load one table's rows and columns for key-value inspection.
  const loadInspectorTable = async (tableName = inspectorSelectedTable) => {
    if (!tableName) return;
    const safeLimit = Math.max(1, Math.min(500, Math.round(inspectorLimit)));
    if (safeLimit !== inspectorLimit) {
      setInspectorLimit(safeLimit);
    }

    setInspectorLoading(true);
    try {
      const data = await adminFetch<{
        columns: InspectorColumn[];
        rows: Record<string, unknown>[];
        total: number;
      }>(`/api/admin/db/table/${encodeURIComponent(tableName)}?limit=${safeLimit}&offset=0`);
      setInspectorColumns(Array.isArray(data.columns) ? data.columns : []);
      setInspectorRows(Array.isArray(data.rows) ? data.rows : []);
      setInspectorTotalRows(Number(data.total ?? 0));
      setInspectorError('');
    } catch (err: any) {
      setInspectorColumns([]);
      setInspectorRows([]);
      setInspectorTotalRows(0);
      setInspectorError(err?.message ?? 'Failed to load table rows.');
    } finally {
      setInspectorLoading(false);
    }
  };

  // Toggle admin-controlled user flags and refresh user list.
  const toggleUserField = async (userId: string, field: 'is_active' | 'is_admin', value: boolean) => {
    try {
      await adminPatch(`/api/admin/users/${userId}?${field}=${value}`);
      loadUsers();
      loadOverview();
    } catch { /* ignore */ }
  };

  const applyTimedBan = async (userId: string) => {
    const rawMinutes = (banMinutesByUser[userId] || '').trim();
    const minutes = Number(rawMinutes || '0');
    if (!Number.isFinite(minutes) || minutes < 1) return;

    const reason = (banReasonByUser[userId] || '').trim();
    const params = new URLSearchParams({
      ban_minutes: String(Math.round(minutes)),
    });
    if (reason) params.set('ban_reason', reason);

    try {
      await adminPatch(`/api/admin/users/${userId}?${params.toString()}`);
      await Promise.all([loadUsers(), loadOverview()]);
    } catch { /* ignore */ }
  };

  const clearTimedBan = async (userId: string) => {
    try {
      await adminPatch(`/api/admin/users/${userId}?clear_ban=true`);
      await Promise.all([loadUsers(), loadOverview()]);
    } catch { /* ignore */ }
  };

  const saveSelectedUserProfile = async () => {
    const targetUserId = selectedUserDetail?.user?.id;
    if (!targetUserId) return;

    const username = selectedUserEditUsername.trim();
    const email = selectedUserEditEmail.trim().toLowerCase();
    const level = selectedUserEditLevel.trim();
    const pointsValue = Number(selectedUserEditPoints.trim());

    if (!username || username.length < 3) {
      setSelectedUserEditError('Username must be at least 3 characters.');
      setSelectedUserEditSuccess('');
      return;
    }
    if (!email || !email.includes('@')) {
      setSelectedUserEditError('Please enter a valid email address.');
      setSelectedUserEditSuccess('');
      return;
    }
    if (!level) {
      setSelectedUserEditError('Level cannot be empty.');
      setSelectedUserEditSuccess('');
      return;
    }
    if (!Number.isFinite(pointsValue) || pointsValue < 0) {
      setSelectedUserEditError('Points must be zero or greater.');
      setSelectedUserEditSuccess('');
      return;
    }

    setSelectedUserEditSaving(true);
    setSelectedUserEditError('');
    setSelectedUserEditSuccess('');
    try {
      await adminPatch(`/api/admin/users/${targetUserId}`, {
        username,
        email,
        level,
        points: Math.round(pointsValue),
      });
      const refreshed = await adminFetch<any>(`/api/admin/users/${targetUserId}`);
      setSelectedUserDetail(refreshed);
      syncSelectedUserEditForm(refreshed);
      setSelectedUserEditSuccess('User profile updated successfully.');
      await Promise.all([loadUsers(), loadOverview()]);
    } catch (err: any) {
      setSelectedUserEditError(err?.message || 'Failed to update user profile.');
    } finally {
      setSelectedUserEditSaving(false);
    }
  };




  const createQuestion = async () => {
    const question_text = newQuestionText.trim();
    const correct_answer = newQuestionAnswer.trim();
    const explanation = newQuestionExplanation.trim();
    const topic = newQuestionTopic.trim();
    const options = newQuestionOptions
      .split(/\r?\n|;/)
      .map((item) => item.trim())
      .filter(Boolean);
    if (!question_text || !correct_answer || !explanation || !topic || options.length < 2) return;

    try {
      await adminPost('/api/admin/questions', {
        question_text,
        correct_answer,
        options,
        explanation,
        topic,
        source: 'admin',
      });
      setNewQuestionText('');
      setNewQuestionOptions('');
      setNewQuestionAnswer('');
      setNewQuestionExplanation('');
      loadQuestions();
      loadOverview();
    } catch { /* ignore */ }
  };

  const editQuestion = async (question: any) => {
    const nextText = window.prompt('Question text', question.question_text || question.text || '')?.trim();
    if (!nextText) return;
    const nextAnswer = window.prompt('Correct answer', question.correct_answer || '')?.trim();
    if (!nextAnswer) return;
    const nextExplanation = window.prompt('Explanation', question.explanation || '')?.trim();
    if (!nextExplanation) return;
    const nextTopic = window.prompt('Topic', question.topic || '')?.trim();
    if (!nextTopic) return;
    const rawOptions = window.prompt(
      'Options (separate with ;)',
      (() => {
        try {
          const parsed = JSON.parse(question.options_json || '[]');
          return Array.isArray(parsed) ? parsed.join('; ') : '';
        } catch {
          return '';
        }
      })()
    ) || '';
    const options = rawOptions.split(';').map((item) => item.trim()).filter(Boolean);
    if (options.length < 2) return;

    try {
      await adminPatch(`/api/admin/questions/${question.id}`, {
        question_text: nextText,
        correct_answer: nextAnswer,
        explanation: nextExplanation,
        topic: nextTopic,
        options,
      });
      loadQuestions();
    } catch { /* ignore */ }
  };

  const deleteQuestion = async (question: any) => {
    const confirmed = window.confirm('Delete this question?');
    if (!confirmed) return;
    try {
      await adminDelete(`/api/admin/questions/${question.id}`);
      loadQuestions();
      loadOverview();
    } catch { /* ignore */ }
  };

  const displayUserName = (u: any): string => {
    const value = (u?.display_name || u?.username || '').toString().trim();
    if (value) return value;
    const local = (u?.email || '').toString().split('@', 1)[0]?.trim();
    if (local) return local;
    return `User ${String(u?.id || '').slice(0, 8)}`;
  };

  // Refresh dataset for whichever tab is currently selected.
  const refreshCurrentTab = () => {
    if (tab === 'overview') loadOverview();
    if (tab === 'users') loadUsers();
    if (tab === 'questions') loadQuestions();
    if (tab === 'sessions') loadSessions();
    if (tab === 'topics') loadCustomTopicCandidates();
    if (tab === 'inspector') {
      loadInspectorSchema();
      if (inspectorSelectedTable) {
        loadInspectorTable(inspectorSelectedTable);
      }
    }
    if (tab === 'monitoring') loadMonitoring();
    if (tab === 'governance') loadGovernance();
  };

  const renderInspectorValue = (value: unknown): string => {
    if (value === null || value === undefined) return '-';
    if (typeof value === 'object') {
      try {
        return JSON.stringify(value);
      } catch {
        return '[object]';
      }
    }
    return String(value);
  };


  // - Styles (Tailwind overhaul) -

  const formatDateTime = (value: string | null | undefined): string => {
    if (!value) return '-';
    const parsed = new Date(value);
    if (Number.isNaN(parsed.getTime())) return value;
    return parsed.toLocaleString();
  };

  const analyticsRows = dailyAnalytics?.items ?? [];
  const maxDailyResponses = Math.max(1, ...analyticsRows.map((r) => r.responses || 0));
  const maxDailySessions = Math.max(
    1,
    ...analyticsRows.map((r) => (r.classic_sessions || 0) + (r.challenge_sessions || 0) + (r.custom_sessions || 0) + (r.pvp_matches || 0))
  );

  const tabBtnClass = (active: boolean) => 
    `px-5 py-2.5 rounded-md text-xs font-bold uppercase tracking-widest transition-all duration-300 ${
      active 
        ? 'bg-[#2D1B14] text-[#F5F2E7] shadow-md' 
        : 'bg-transparent text-[#2D1B14]/50 hover:bg-[#2D1B14]/[0.06] hover:text-[#2D1B14]/80'
    }`;

  const cardClass = "bg-white p-6 rounded-lg border border-[#2D1B14]/8 shadow-sm";
  
  const thClass = "text-left p-3 text-[10px] font-bold uppercase tracking-widest text-[#2D1B14]/50 border-b border-[#2D1B14]/8 bg-[#FDFCF7]";
  const tdClass = "p-3 text-sm text-[#2D1B14] border-b border-[#2D1B14]/[0.04]";

  const statCard = (label: string, value: number | string, isAlert: boolean = false) => (
    <div className={`${cardClass} text-center flex flex-col justify-center items-center`}>
      <div className={`text-3xl font-black font-playfair ${isAlert ? 'text-[#e74c3c]' : 'text-[#2D1B14]'}`}>{value}</div>
      <div className="text-[10px] font-bold uppercase tracking-widest text-[#D4AF37] mt-2">{label}</div>
    </div>
  );

  return (
    <InternalLayout>
      <div className="mx-auto p-8">
        <div className="flex justify-between items-end mb-10">
          <div>
            <h1 className="text-4xl font-black font-playfair text-[#2D1B14] mb-2">Admin Dashboard</h1>
            <p className="text-[#2D1B14]/60 italic">System overview and governance controls.</p>
          </div>
          <button
            onClick={refreshCurrentTab}
            className="px-6 py-3 bg-[#D4AF37] text-white text-xs font-bold uppercase tracking-[0.2em] rounded-md shadow-lg hover:bg-[#c29e2e] transition-all duration-300 hover:shadow-xl hover:-translate-y-0.5"
          >
            Refresh Data
          </button>
        </div>

        {/* Tab Bar */}
        <div className="flex gap-2 mb-10 flex-wrap border-b border-[#2D1B14]/8 pb-4">
          {(['overview', 'users', 'questions', 'sessions', 'topics', 'governance', 'inspector', 'monitoring'] as Tab[]).map(t => (
            <button key={t} onClick={() => setTab(t)} className={tabBtnClass(tab === t)}>
              {t}
            </button>
          ))}
        </div>

        {/* - OVERVIEW - */}
        {tab === 'overview' && overviewError && (
          <div className="mb-6 p-4 bg-amber-50 border border-amber-200 text-amber-700 text-sm rounded">
            {overviewError}
          </div>
        )}
        {tab === 'overview' && !overview && !overviewError && (
          <div className="mb-6 p-4 text-[#2D1B14]/50 text-sm italic">Loading overview…</div>
        )}
        {tab !== 'overview' && tabError && (
          <div className="mb-6 p-4 bg-amber-50 border border-amber-200 text-amber-700 text-sm rounded">
            {tabError}
          </div>
        )}
        {tab === 'overview' && overview && (
          <div className="space-y-8">
            <div className="grid grid-cols-2 md:grid-cols-4 gap-6">
              {statCard('Total Users', overview.users.total)}
              {statCard('Active Users', overview.users.active)}
              {statCard('Admin Users', overview.users.admin ?? 0)}
              {statCard('Banned Users', overview.users.banned ?? 0, (overview.users.banned ?? 0) > 0)}
              {statCard('Total Questions', overview.questions.total)}
              {statCard('Generated Questions', overview.questions.generated ?? 0)}
              {statCard('Seed Questions', overview.questions.seeded ?? 0)}
              {statCard('Unknown Sources', overview.questions.unknown ?? 0, (overview.questions.unknown ?? 0) > 0)}
              {statCard('Total Responses', overview.responses.total)}
              {statCard('Concepts Tracked', overview.concepts.total)}
              {statCard('Classic Sessions', overview.sessions.classic ?? 0)}
              {statCard('Challenge Sessions', overview.sessions.challenge)}
              {statCard('Custom Sessions', overview.sessions.custom)}
              {statCard('PvP Matches', overview.pvp.total_matches)}
            </div>

            {dailyAnalytics && analyticsRows.length > 0 && (
              <div className={`${cardClass} space-y-6`}>
                <div className="flex flex-wrap items-center justify-between gap-3">
                  <h3 className="text-xs font-bold uppercase tracking-[0.3em] text-[#D4AF37]">Daily Activity (Last {dailyAnalytics.days} Days)</h3>
                  <div className="text-xs text-[#2D1B14]/60">
                    {dailyAnalytics.start_date} to {dailyAnalytics.end_date}
                  </div>
                </div>

                <div className="grid grid-cols-1 lg:grid-cols-2 gap-8">
                  <div>
                    <div className="text-[10px] font-bold uppercase tracking-widest text-[#2D1B14]/50 mb-3">Responses Per Day</div>
                    <div className="h-40 flex items-end gap-2">
                      {analyticsRows.map((row) => {
                        const barHeight = Math.max(8, Math.round((row.responses / maxDailyResponses) * 120));
                        const dayLabel = row.date.slice(5);
                        return (
                          <div key={`resp-${row.date}`} className="flex-1 min-w-0 flex flex-col items-center gap-1">
                            <div
                              className="w-full bg-[#D4AF37]/85 rounded-t"
                              style={{ height: `${barHeight}px` }}
                              title={`${row.date}: ${row.responses} responses`}
                            />
                            <div className="text-[9px] text-[#2D1B14]/50">{dayLabel}</div>
                          </div>
                        );
                      })}
                    </div>
                  </div>

                  <div>
                    <div className="text-[10px] font-bold uppercase tracking-widest text-[#2D1B14]/50 mb-3">Sessions Per Day (All Rooms)</div>
                    <div className="h-40 flex items-end gap-2">
                      {analyticsRows.map((row) => {
                        const totalSessions =
                          (row.classic_sessions || 0) +
                          (row.challenge_sessions || 0) +
                          (row.custom_sessions || 0) +
                          (row.pvp_matches || 0);
                        const barHeight = Math.max(8, Math.round((totalSessions / maxDailySessions) * 120));
                        const dayLabel = row.date.slice(5);
                        return (
                          <div key={`sess-${row.date}`} className="flex-1 min-w-0 flex flex-col items-center gap-1">
                            <div
                              className="w-full bg-[#2D1B14]/75 rounded-t"
                              style={{ height: `${barHeight}px` }}
                              title={`${row.date}: ${totalSessions} sessions`}
                            />
                            <div className="text-[9px] text-[#2D1B14]/50">{dayLabel}</div>
                          </div>
                        );
                      })}
                    </div>
                  </div>
                </div>

                {dailyAnalytics.top_users.length > 0 && (
                  <div className="overflow-x-auto">
                    <div className="text-[10px] font-bold uppercase tracking-widest text-[#2D1B14]/50 mb-2">Top Active Users</div>
                    <table className="w-full text-left border-collapse">
                      <thead>
                        <tr>
                          <th className={thClass}>User</th>
                          <th className={thClass}>Email</th>
                          <th className={`${thClass} text-center`}>Responses</th>
                          <th className={`${thClass} text-center`}>Correct</th>
                          <th className={`${thClass} text-center`}>Accuracy</th>
                          <th className={`${thClass} text-center`}>Points</th>
                        </tr>
                      </thead>
                      <tbody>
                        {dailyAnalytics.top_users.map((u) => (
                          <tr key={u.user_id} className="hover:bg-[#2D1B14]/5 transition-colors">
                            <td className={`${tdClass} font-bold`}>{u.display_name || u.username || `User ${u.user_id.slice(0, 8)}`}</td>
                            <td className={`${tdClass} text-[#2D1B14]/60`}>{u.email || '-'}</td>
                            <td className={`${tdClass} text-center`}>{u.responses}</td>
                            <td className={`${tdClass} text-center`}>{u.correct}</td>
                            <td className={`${tdClass} text-center text-[#D4AF37] font-bold`}>{u.accuracy.toFixed(1)}%</td>
                            <td className={`${tdClass} text-center font-bold`}>{u.points}</td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                )}
              </div>
            )}

            {/* Top Concepts */}
            {topConcepts.length > 0 && (
              <div className={cardClass}>
                <h3 className="text-xs font-bold uppercase tracking-[0.3em] text-[#D4AF37] mb-6">Top Concepts</h3>
                <div className="space-y-3">
                  {topConcepts.map((c, i) => (
                    <div key={c.concept_id} className={`flex justify-between items-center py-3 ${i < topConcepts.length - 1 ? 'border-b border-[#2D1B14]/5' : ''}`}>
                      <div>
                        <span className="font-bold text-[#2D1B14]">{c.name}</span>
                        <span className="text-[#2D1B14]/50 ml-2 text-sm italic">({c.topic})</span>
                      </div>
                      <div className="text-sm text-[#2D1B14]/70">
                        {c.tracked_users} users | <span className="text-[#D4AF37] font-bold">theta={c.avg_theta}</span>
                      </div>
                    </div>
                  ))}
                </div>
              </div>
            )}
          </div>
        )}

        {/* - USERS - */}
        {tab === 'users' && (
          <div className={cardClass}>
            <div className="mb-6 flex gap-4">
              <input
                value={userSearch}
                onChange={e => { setUserSearch(e.target.value); setUserPage(1); }}
                placeholder="Search by email or username..."
                className="flex-1 p-3 border border-[#2D1B14]/20 rounded bg-transparent focus:border-[#D4AF37] outline-none text-[#2D1B14]"
              />
            </div>

            <div className="overflow-x-auto">
              <table className="w-full text-left border-collapse">
                <thead>
                  <tr>
                    <th className={thClass}>Name</th>
                    <th className={thClass}>Email</th>
                    <th className={thClass}>Level</th>
                    <th className={`${thClass} text-center`}>Points</th>
                    <th className={`${thClass} text-center`}>Active</th>
                    <th className={`${thClass} text-center`}>Admin</th>
                    <th className={thClass}>Ban Control</th>
                  </tr>
                </thead>
                <tbody>
                  {users.map(u => (
                    <tr key={u.id} className="hover:bg-[#2D1B14]/5 transition-colors">
                      <td className={tdClass}>
                        <div className="font-bold">{displayUserName(u)}</div>
                        <div className="text-xs text-[#2D1B14]/50">@{(u.username || '').trim() || 'no-username'}</div>
                        <button
                          onClick={() => openUserDetail(u.id)}
                          className="mt-2 text-[10px] font-bold uppercase tracking-widest text-[#2D1B14]/70 hover:text-[#D4AF37]"
                        >
                          View Profile
                        </button>
                      </td>
                      <td className={`${tdClass} text-[#2D1B14]/60`}>{u.email}</td>
                      <td className={tdClass}>{u.level || '-'}</td>
                      <td className={`${tdClass} text-center font-playfair font-bold text-lg`}>{u.points}</td>
                      <td className={`${tdClass} text-center`}>
                        <button onClick={() => toggleUserField(u.id, 'is_active', !u.is_active)} className={`text-xs font-bold uppercase tracking-wider ${u.is_active ? 'text-green-600' : 'text-red-600'}`}>
                          {u.is_active ? 'Active' : 'Disabled'}
                        </button>
                      </td>
                      <td className={`${tdClass} text-center`}>
                        <button onClick={() => toggleUserField(u.id, 'is_admin', !u.is_admin)} className={`text-xs font-bold uppercase tracking-wider ${u.is_admin ? 'text-[#D4AF37]' : 'text-gray-400'}`}>
                          {u.is_admin ? 'Admin' : 'User'}
                        </button>
                      </td>
                      <td className={tdClass}>
                        <div className="text-xs mb-2">
                          {u.is_banned_now ? (
                            <span className="text-red-600 font-bold">Banned until {formatDateTime(u.ban_until)}</span>
                          ) : (
                            <span className="text-[#2D1B14]/50">Not banned</span>
                          )}
                        </div>
                        <div className="flex flex-wrap gap-2">
                          <input
                            value={banMinutesByUser[u.id] ?? ''}
                            onChange={(e) => setBanMinutesByUser((prev) => ({ ...prev, [u.id]: e.target.value }))}
                            placeholder="minutes"
                            className="w-20 p-1.5 border border-[#2D1B14]/20 rounded text-xs"
                          />
                          <input
                            value={banReasonByUser[u.id] ?? ''}
                            onChange={(e) => setBanReasonByUser((prev) => ({ ...prev, [u.id]: e.target.value }))}
                            placeholder="reason"
                            className="min-w-[140px] flex-1 p-1.5 border border-[#2D1B14]/20 rounded text-xs"
                          />
                          <button
                            onClick={() => applyTimedBan(u.id)}
                            className="px-2.5 py-1.5 bg-[#e74c3c] text-white text-[10px] font-bold uppercase tracking-widest rounded"
                          >
                            Ban
                          </button>
                          {u.is_banned_now && (
                            <button
                              onClick={() => clearTimedBan(u.id)}
                              className="px-2.5 py-1.5 border border-[#2D1B14]/20 text-[10px] font-bold uppercase tracking-widest rounded hover:border-[#D4AF37]"
                            >
                              Clear
                            </button>
                          )}
                        </div>
                        {u.ban_reason && <div className="text-xs text-[#2D1B14]/60 mt-1">Reason: {u.ban_reason}</div>}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>

            <div className="flex justify-between items-center mt-6 text-xs font-bold uppercase tracking-widest text-[#2D1B14]/60">
              <span>{usersTotal} total users</span>
              <div className="flex gap-4 items-center">
                <button onClick={() => setUserPage(p => Math.max(1, p - 1))} disabled={userPage <= 1} className="hover:text-[#D4AF37] disabled:opacity-30">Prev</button>
                <span className="text-[#D4AF37]">Page {userPage}</span>
                <button onClick={() => setUserPage(p => p + 1)} disabled={users.length < 15} className="hover:text-[#D4AF37] disabled:opacity-30">Next</button>
              </div>
            </div>
            {(selectedUserLoading || selectedUserError || selectedUserDetail) && (
              <div className="mt-6 border border-[#2D1B14]/15 rounded p-4 bg-[#FAF8F3]">
                <div className="flex items-center justify-between mb-3">
                  <div className="text-xs font-bold uppercase tracking-widest text-[#2D1B14]/60">User Profile Inspector</div>
                  <button onClick={closeUserDetail} className="text-xs font-bold uppercase tracking-widest text-[#2D1B14]/50 hover:text-[#D4AF37]">Close</button>
                </div>

                {selectedUserLoading && <div className="text-sm text-[#2D1B14]/60">Loading user details...</div>}
                {selectedUserError && <div className="text-sm text-red-600">{selectedUserError}</div>}

                {selectedUserDetail && (
                  <div className="space-y-4">
                    <div className="grid grid-cols-1 md:grid-cols-3 gap-3 text-sm">
                      <div><span className="text-[#2D1B14]/50 uppercase text-[10px] tracking-widest">Name</span><div className="font-bold">{displayUserName(selectedUserDetail.user || {})}</div></div>
                      <div><span className="text-[#2D1B14]/50 uppercase text-[10px] tracking-widest">Email</span><div>{selectedUserDetail.user?.email || '-'}</div></div>
                      <div><span className="text-[#2D1B14]/50 uppercase text-[10px] tracking-widest">Joined</span><div>{formatDateTime(selectedUserDetail.user?.created_at)}</div></div>
                    </div>

                    <div className="border border-[#2D1B14]/15 rounded p-4 bg-white">
                      <div className="text-xs font-bold uppercase tracking-widest text-[#2D1B14]/60 mb-3">Edit User Profile</div>
                      <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
                        <input
                          value={selectedUserEditUsername}
                          onChange={(e) => setSelectedUserEditUsername(e.target.value)}
                          placeholder="Username"
                          className="p-2 border border-[#2D1B14]/20 rounded text-sm"
                        />
                        <input
                          value={selectedUserEditEmail}
                          onChange={(e) => setSelectedUserEditEmail(e.target.value)}
                          placeholder="Email"
                          type="email"
                          className="p-2 border border-[#2D1B14]/20 rounded text-sm"
                        />
                        <input
                          value={selectedUserEditLevel}
                          onChange={(e) => setSelectedUserEditLevel(e.target.value)}
                          placeholder="Level"
                          className="p-2 border border-[#2D1B14]/20 rounded text-sm"
                        />
                        <input
                          value={selectedUserEditPoints}
                          onChange={(e) => setSelectedUserEditPoints(e.target.value)}
                          placeholder="Points"
                          type="number"
                          min={0}
                          className="p-2 border border-[#2D1B14]/20 rounded text-sm"
                        />
                      </div>
                      {selectedUserEditError && (
                        <div className="mt-3 text-sm text-red-600">{selectedUserEditError}</div>
                      )}
                      {selectedUserEditSuccess && (
                        <div className="mt-3 text-sm text-green-700">{selectedUserEditSuccess}</div>
                      )}
                      <div className="mt-3">
                        <button
                          onClick={saveSelectedUserProfile}
                          disabled={selectedUserEditSaving}
                          className="px-4 py-2 bg-[#2D1B14] text-white text-xs font-bold uppercase tracking-widest rounded hover:bg-[#3d261c] disabled:opacity-50"
                        >
                          {selectedUserEditSaving ? 'Saving...' : 'Save Profile'}
                        </button>
                      </div>
                    </div>

                    <div className="grid grid-cols-2 md:grid-cols-6 gap-2">
                      {statCard('Responses', selectedUserDetail.stats?.total_responses ?? 0)}
                      {statCard('Correct', selectedUserDetail.stats?.correct_responses ?? 0)}
                      {statCard('Accuracy', `${Number(selectedUserDetail.stats?.accuracy ?? 0).toFixed(1)}%`)}
                      {statCard('Classic', selectedUserDetail.stats?.classic_sessions ?? 0)}
                      {statCard('Challenge', selectedUserDetail.stats?.challenge_sessions ?? 0)}
                      {statCard('Custom', selectedUserDetail.stats?.custom_sessions ?? 0)}
                    </div>

                    <div className="grid grid-cols-2 md:grid-cols-4 gap-2">
                      {statCard('PvP Matches', selectedUserDetail.pvp?.matches ?? 0)}
                      {statCard('PvP Wins', selectedUserDetail.pvp?.wins ?? 0)}
                      {statCard('PvP Losses', selectedUserDetail.pvp?.losses ?? 0)}
                      {statCard('PvP Elo', Number(selectedUserDetail.pvp?.elo_rating ?? 0).toFixed(0))}
                    </div>

                    <div>
                      <div className="text-xs font-bold uppercase tracking-widest text-[#2D1B14]/60 mb-2">Recent Sessions</div>
                      {(selectedUserDetail.sessions?.recent || []).length === 0 && (
                        <div className="text-sm text-[#2D1B14]/50 italic">No recent sessions.</div>
                      )}
                      {(selectedUserDetail.sessions?.recent || []).slice(0, 6).map((s: any) => (
                        <div key={s.id} className="flex justify-between items-center text-xs py-1 border-b border-[#2D1B14]/8">
                          <span className="uppercase tracking-widest text-[#2D1B14]/60">{s.type}</span>
                          <span>{s.topic || '-'}</span>
                          <span>{s.correct ?? 0}/{s.questions ?? 0}</span>
                          <span>{formatDateTime(s.created_at)}</span>
                        </div>
                      ))}
                    </div>
                  </div>
                )}
              </div>
            )}
          </div>
        )}

        {/* - QUESTIONS - */}
        {tab === 'questions' && (
          <div className="space-y-6">
            <div className="flex gap-3 mb-4">
              {['', 'history', 'geography'].map(t => (
                <button key={t} onClick={() => { setQTopic(t); setQPage(1); }} className={`px-4 py-2 rounded text-xs uppercase tracking-widest font-bold border ${qTopic === t ? 'bg-[#D4AF37] text-white border-[#D4AF37]' : 'border-[#2D1B14]/20 text-[#2D1B14]/60 hover:border-[#D4AF37]'}`}>
                  {t || 'All Topics'}
                </button>
              ))}
            </div>

            <div className={cardClass}>
              <div className="text-[10px] font-bold uppercase tracking-widest text-[#D4AF37] mb-3">Create Question</div>
              <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
                <input
                  value={newQuestionTopic}
                  onChange={(e) => setNewQuestionTopic(e.target.value)}
                  placeholder="topic (history/geography)"
                  className="p-2 border border-[#2D1B14]/20 rounded text-sm"
                />
                <input
                  value={newQuestionAnswer}
                  onChange={(e) => setNewQuestionAnswer(e.target.value)}
                  placeholder="correct answer"
                  className="p-2 border border-[#2D1B14]/20 rounded text-sm"
                />
                <textarea
                  value={newQuestionText}
                  onChange={(e) => setNewQuestionText(e.target.value)}
                  placeholder="question text"
                  className="p-2 border border-[#2D1B14]/20 rounded text-sm md:col-span-2 min-h-[70px]"
                />
                <textarea
                  value={newQuestionOptions}
                  onChange={(e) => setNewQuestionOptions(e.target.value)}
                  placeholder="options (one per line or separated by ;)"
                  className="p-2 border border-[#2D1B14]/20 rounded text-sm min-h-[70px]"
                />
                <textarea
                  value={newQuestionExplanation}
                  onChange={(e) => setNewQuestionExplanation(e.target.value)}
                  placeholder="explanation"
                  className="p-2 border border-[#2D1B14]/20 rounded text-sm min-h-[70px]"
                />
              </div>
              <div className="mt-3">
                <button onClick={createQuestion} className="px-4 py-2 bg-[#2D1B14] text-white text-xs font-bold uppercase tracking-widest rounded hover:bg-[#3d261c]">Add Question</button>
              </div>
            </div>

            <div className="space-y-4">
              {questions.map(q => (
                <div key={q.id} className={`${cardClass} flex justify-between items-center`}>
                  <div className="flex-1">
                    <div className="text-[#2D1B14] font-medium">{q.question_text || q.text || '(no question text)'}</div>
                    <div className="flex gap-4 mt-3 text-xs uppercase tracking-widest text-[#2D1B14]/50">
                      <span><span className="text-[#D4AF37]">Topic:</span> {q.topic}</span>
                      <span><span className="text-[#D4AF37]">IRT:</span> {q.difficulty_irt}</span>
                      <span><span className="text-[#D4AF37]">Seen:</span> {q.times_seen}x</span>
                      {q.gov_approved === false && <span className="bg-red-100 text-red-600 px-2 py-0.5 rounded font-bold">BLOCKED</span>}
                      {q.gov_approved === true && <span className="bg-green-100 text-green-600 px-2 py-0.5 rounded font-bold">APPROVED</span>}
                    </div>
                  </div>
                  <div className="ml-4 flex gap-2">
                    <button
                      onClick={() => editQuestion(q)}
                      className="px-3 py-1.5 border border-[#2D1B14]/20 text-[10px] font-bold uppercase tracking-widest rounded hover:border-[#D4AF37]"
                    >
                      Edit
                    </button>
                    <button
                      onClick={() => deleteQuestion(q)}
                      className="px-3 py-1.5 bg-[#e74c3c] text-white text-[10px] font-bold uppercase tracking-widest rounded"
                    >
                      Delete
                    </button>
                  </div>
                </div>
              ))}
            </div>

            <div className="flex justify-between items-center mt-6 text-xs font-bold uppercase tracking-widest text-[#2D1B14]/60">
              <span>{qTotal} total questions</span>
              <div className="flex gap-4 items-center">
                <button onClick={() => setQPage(p => Math.max(1, p - 1))} disabled={qPage <= 1} className="hover:text-[#D4AF37] disabled:opacity-30">Prev</button>
                <span className="text-[#D4AF37]">Page {qPage}</span>
                <button onClick={() => setQPage(p => p + 1)} disabled={questions.length < 15} className="hover:text-[#D4AF37] disabled:opacity-30">Next</button>
              </div>
            </div>
          </div>
        )}

        {/* - SESSIONS - */}
        {tab === 'sessions' && (
          <div className="space-y-4">
            {sessions.length === 0 && <p className="text-[#2D1B14]/50 italic">No sessions found</p>}
            {sessions.map(s => (
              <div
                key={s.id}
                onClick={() => loadSessionDetails(s.id, s.type)}
                className={`${cardClass} flex justify-between items-center cursor-pointer hover:border-[#D4AF37]/45 hover:shadow-md transition-all`}
                title="Click to inspect detailed session logs"
              >
                <div className="flex items-center gap-4">
                  <span className={`px-3 py-1 text-xs font-bold uppercase tracking-widest rounded ${s.type === 'challenge' ? 'bg-red-100 text-red-600' : 'bg-blue-100 text-blue-600'}`}>
                    {s.type}
                  </span>
                  <div>
                    <div className="text-[#2D1B14] font-medium">{s.topic}</div>
                    {s.type === 'pvp' ? (
                      <div className="text-xs text-[#2D1B14]/60">
                        {s.user1_name || s.user1_id} vs {s.user2_name || s.user2_id}
                      </div>
                    ) : (
                      <div className="text-xs text-[#2D1B14]/60">{s.user_name || s.user_id}</div>
                    )}
                  </div>
                </div>
                <div className="text-sm font-bold text-[#2D1B14]/60 flex items-center gap-4">
                  <div>
                    <span className="text-[#D4AF37] text-lg">{s.correct}</span> / {s.questions} correct
                  </div>
                  <span className={`px-2 py-0.5 rounded text-[10px] font-bold uppercase tracking-widest ${s.is_completed ? 'bg-green-50 text-green-600' : 'bg-yellow-50 text-yellow-600'}`}>
                    {s.is_completed ? 'Complete' : 'Pending'}
                  </span>
                  <button className="px-2.5 py-1 text-[10px] font-bold uppercase tracking-widest border border-[#2D1B14]/20 hover:border-[#D4AF37] rounded transition-colors bg-white">
                    Inspect Logs
                  </button>
                </div>
              </div>
            ))}
          </div>
        )}

        {/* CUSTOM TOPICS */}
        {tab === 'topics' && (
          <div className="space-y-6">
            {/* Create Manual Topic Form */}
            <div className={cardClass}>
              <h3 className="text-xs font-bold uppercase tracking-[0.3em] text-[#D4AF37] mb-4">Create Manual Custom Topic</h3>
              <form onSubmit={handleCreateManualTopic} className="grid grid-cols-1 gap-4 md:grid-cols-3">
                <div className="flex flex-col gap-1">
                  <label className="text-[10px] font-bold uppercase tracking-widest text-[#2D1B14]/60">Topic Type</label>
                  <select
                    value={manualTopicType}
                    onChange={(e) => setManualTopicType(e.target.value)}
                    className="p-2 border border-[#2D1B14]/20 rounded text-sm bg-transparent outline-none focus:border-[#D4AF37]"
                  >
                    <option value="History">History</option>
                    <option value="Geography">Geography</option>
                  </select>
                </div>
                <div className="flex flex-col gap-1">
                  <label className="text-[10px] font-bold uppercase tracking-widest text-[#2D1B14]/60">Topic Name</label>
                  <input
                    value={manualTopicName}
                    onChange={(e) => setManualTopicName(e.target.value)}
                    placeholder="e.g. World War III"
                    className="p-2 border border-[#2D1B14]/20 rounded text-sm outline-none focus:border-[#D4AF37]"
                    required
                  />
                </div>
                <div className="flex flex-col gap-1">
                  <label className="text-[10px] font-bold uppercase tracking-widest text-[#2D1B14]/60">Source Family (Optional)</label>
                  <input
                    value={manualTopicSource}
                    onChange={(e) => setManualTopicSource(e.target.value)}
                    placeholder="e.g. History (for automated fact harvesting)"
                    className="p-2 border border-[#2D1B14]/20 rounded text-sm outline-none focus:border-[#D4AF37]"
                  />
                </div>
                <div className="flex flex-col gap-1 md:col-span-3">
                  <label className="text-[10px] font-bold uppercase tracking-widest text-[#2D1B14]/60">Description (Optional)</label>
                  <input
                    value={manualTopicDesc}
                    onChange={(e) => setManualTopicDesc(e.target.value)}
                    placeholder="Brief description for Custom Room selection screen"
                    className="p-2 border border-[#2D1B14]/20 rounded text-sm outline-none focus:border-[#D4AF37]"
                  />
                </div>
                <div className="flex flex-col gap-1 md:col-span-3">
                  <label className="text-[10px] font-bold uppercase tracking-widest text-[#2D1B14]/60">Custom Context / Facts (Optional, Multi-sentence text block)</label>
                  <textarea
                    value={manualTopicContext}
                    onChange={(e) => setManualTopicContext(e.target.value)}
                    placeholder="Paste paragraphs or facts here. Sentences will be parsed and inserted as Facts in the DB."
                    className="p-2 border border-[#2D1B14]/20 rounded text-sm h-24 outline-none focus:border-[#D4AF37]"
                  />
                </div>
                <div className="md:col-span-3 flex justify-end">
                  <button
                    type="submit"
                    disabled={manualTopicLoading}
                    className="px-4 py-2 bg-[#D4AF37] text-white text-xs font-bold uppercase tracking-widest rounded hover:bg-[#c29e2e] disabled:opacity-50"
                  >
                    {manualTopicLoading ? 'Creating...' : 'Create Topic'}
                  </button>
                </div>
              </form>
            </div>

            <div className={cardClass}>
              <div className="flex flex-wrap items-start justify-between gap-4 mb-6">
                <div>
                  <h3 className="text-xs font-bold uppercase tracking-[0.3em] text-[#D4AF37] mb-2">Custom Topic Catalogue</h3>
                  <p className="text-sm text-[#2D1B14]/60">
                    Approve Custom Room topics from the built-in catalogue or existing question-bank coverage.
                  </p>
                </div>
                <button
                  onClick={loadCustomTopicCandidates}
                  disabled={customTopicLoading}
                  className="px-4 py-2 bg-[#2D1B14] text-white text-xs font-bold uppercase tracking-widest rounded hover:bg-[#3d261c] disabled:opacity-50"
                >
                  {customTopicLoading ? 'Loading' : 'Reload Candidates'}
                </button>
              </div>

              {customTopicError && (
                <div className="mb-4 p-3 bg-red-50 border border-red-200 text-red-700 rounded text-sm font-bold">
                  {customTopicError}
                </div>
              )}
              {customTopicSuccess && (
                <div className="mb-4 p-3 bg-green-50 border border-green-200 text-green-700 rounded text-sm font-bold">
                  {customTopicSuccess}
                </div>
              )}
              {lastCustomTopicApproval && (
                <div className="mb-4 text-xs text-[#2D1B14]/60">
                  Latest approval: <span className="font-bold text-[#2D1B14]">{lastCustomTopicApproval.slug}</span>
                </div>
              )}

              <div className="overflow-x-auto">
                <table className="w-full text-left border-collapse">
                  <thead>
                    <tr>
                      <th className={thClass}>Topic</th>
                      <th className={thClass}>Source</th>
                      <th className={`${thClass} text-center`}>Eligible Questions</th>
                      <th className={`${thClass} text-center`}>Status</th>
                      <th className={`${thClass} text-center`}>Actions</th>
                    </tr>
                  </thead>
                  <tbody>
                    {customTopicCandidates.map(candidate => {
                      const isApproving = customTopicApprovingSlug === candidate.slug;
                      const canApprove = !candidate.approved && candidate.available_question_count > 0 && !isApproving;
                      return (
                        <tr key={candidate.slug} className="hover:bg-[#2D1B14]/5 transition-colors">
                          <td className={tdClass}>
                            <div className="font-bold">{candidate.name}</div>
                            <div className="text-[10px] uppercase tracking-widest text-[#2D1B14]/50">
                              {candidate.type} / {candidate.slug}
                            </div>
                            {candidate.description && (
                              <div className="text-xs text-[#2D1B14]/60 mt-1 max-w-xl">
                                {candidate.description}
                              </div>
                            )}
                          </td>
                          <td className={tdClass}>
                            <span className="px-2 py-1 text-[10px] font-bold uppercase tracking-widest rounded bg-[#2D1B14]/5 text-[#2D1B14]/70">
                              {candidate.candidate_source}
                            </span>
                            <div className="text-xs text-[#2D1B14]/50 mt-2">{candidate.source_topic}</div>
                          </td>
                          <td className={`${tdClass} text-center font-bold`}>
                            {candidate.available_question_count}
                          </td>
                          <td className={`${tdClass} text-center`}>
                            {candidate.approved ? (
                              <span className={`px-3 py-1 text-[10px] font-bold uppercase tracking-widest rounded ${
                                candidate.is_active !== false
                                  ? 'bg-green-100 text-green-700'
                                  : 'bg-red-100 text-red-700 border border-red-200'
                              }`}>
                                {candidate.is_active !== false 
                                  ? `Active (${candidate.total_facts_count ?? 0} Facts)` 
                                  : `Deactivated (${candidate.total_facts_count ?? 0} Facts)`}
                              </span>
                            ) : (
                              <span className={`px-3 py-1 text-[10px] font-bold uppercase tracking-widest rounded ${
                                candidate.candidate_source === 'catalogue'
                                  ? candidate.is_active !== false
                                    ? 'bg-green-100 text-green-700'
                                    : 'bg-red-100 text-red-700 border border-red-200'
                                  : candidate.available_question_count > 0
                                    ? 'bg-yellow-100 text-yellow-700'
                                    : 'bg-gray-100 text-gray-500'
                              }`}>
                                {candidate.candidate_source === 'catalogue'
                                  ? candidate.is_active !== false ? 'Catalog (Active)' : 'Deactivated'
                                  : candidate.available_question_count > 0
                                    ? 'Ready'
                                    : 'No Facts'}
                              </span>
                            )}
                          </td>
                          <td className={`${tdClass} text-center`}>
                            <div className="flex justify-center items-center gap-2">
                              {!candidate.approved ? (
                                <>
                                  <button
                                    onClick={() => approveTopicCandidate(candidate)}
                                    disabled={!canApprove}
                                    className="px-3 py-2 bg-[#D4AF37] text-white text-[10px] font-bold uppercase tracking-widest rounded hover:bg-[#c29e2e] disabled:bg-gray-200 disabled:text-gray-500 disabled:cursor-not-allowed"
                                  >
                                    {isApproving ? 'Approving' : 'Approve'}
                                  </button>
                                  {candidate.candidate_source === 'catalogue' && (
                                    <button
                                      onClick={() => handleToggleTopicActive(candidate, candidate.is_active === false)}
                                      className={`px-3 py-2 text-[10px] font-bold uppercase tracking-widest rounded transition-colors ${
                                        candidate.is_active !== false
                                          ? 'border border-red-600 text-red-600 bg-red-50 hover:bg-red-100'
                                          : 'border border-green-600 text-green-600 bg-green-50 hover:bg-green-100'
                                      }`}
                                    >
                                      {candidate.is_active !== false ? 'Deactivate' : 'Activate'}
                                    </button>
                                  )}
                                </>
                              ) : (
                                <div className="flex gap-2">
                                  <button
                                    onClick={() => approveTopicCandidate(candidate)}
                                    disabled={isApproving}
                                    className="px-3 py-2 border border-[#D4AF37] text-[#D4AF37] bg-[#D4AF37]/5 text-[10px] font-bold uppercase tracking-widest rounded hover:bg-[#D4AF37]/10"
                                  >
                                    {isApproving ? 'Refreshing' : 'Refresh Facts'}
                                  </button>
                                  <button
                                    onClick={() => handleToggleTopicActive(candidate, candidate.is_active === false)}
                                    className={`px-3 py-2 text-[10px] font-bold uppercase tracking-widest rounded transition-colors ${
                                      candidate.is_active !== false
                                        ? 'border border-red-600 text-red-600 bg-red-50 hover:bg-red-100'
                                        : 'border border-green-600 text-green-600 bg-green-50 hover:bg-green-100'
                                    }`}
                                  >
                                    {candidate.is_active !== false ? 'Deactivate' : 'Activate'}
                                  </button>
                                </div>
                              )}
                            </div>
                          </td>
                        </tr>
                      );
                    })}
                  </tbody>
                </table>
              </div>

              {!customTopicLoading && customTopicCandidates.length === 0 && (
                <div className="py-8 text-center text-sm text-[#2D1B14]/50 italic">
                  No custom topic candidates are available.
                </div>
              )}
            </div>
          </div>
        )}

        {/* - GOVERNANCE - */}
        {tab === 'governance' && (
          <div className="space-y-8">
            {govAcceptance && (
              <div className="grid grid-cols-2 md:grid-cols-4 gap-6">
                {statCard('Total Evaluated', govAcceptance.total)}
                {statCard('Approved', govAcceptance.approved)}
                {statCard('Rejected', govAcceptance.total - govAcceptance.approved, true)}
                {statCard('Acceptance Rate', govAcceptance.rate ? `${(govAcceptance.rate * 100).toFixed(1)}%` : '-')}
              </div>
            )}

            <div className={cardClass}>
              <h3 className="text-xs font-bold uppercase tracking-[0.3em] text-[#D4AF37] mb-6">Block Rules</h3>
              
              <div className="flex gap-4 mb-6">
                <select
                  value={govNewKind}
                  onChange={e => setGovNewKind(e.target.value)}
                  className="p-3 border border-[#2D1B14]/20 rounded bg-transparent outline-none text-[#2D1B14]"
                >
                  <option value="keyword">Keyword</option>
                  <option value="topic">Topic</option>
                </select>
                <input
                  value={govNewPattern}
                  onChange={e => setGovNewPattern(e.target.value)}
                  placeholder="Pattern to block..."
                  onKeyDown={e => e.key === 'Enter' && createGovRule()}
                  className="flex-1 p-3 border border-[#2D1B14]/20 rounded bg-transparent focus:border-[#D4AF37] outline-none text-[#2D1B14]"
                />
                <button onClick={createGovRule} className="px-6 py-3 bg-[#e74c3c] text-white text-xs font-bold uppercase tracking-widest rounded hover:bg-[#c0392b] transition-colors">
                  + Add Rule
                </button>
              </div>

              {govRules.length > 0 && (
                <table className="w-full text-left border-collapse mt-4">
                  <thead>
                    <tr>
                      <th className={thClass}>Kind</th>
                      <th className={thClass}>Pattern</th>
                      <th className={`${thClass} text-center`}>Active</th>
                      <th className={`${thClass} text-center`}>Actions</th>
                    </tr>
                  </thead>
                  <tbody>
                    {govRules.map(r => (
                      <tr key={r.id} className="hover:bg-[#2D1B14]/5">
                        <td className={tdClass}>
                          <span className={`px-2 py-1 text-[10px] font-bold uppercase tracking-widest rounded ${r.kind === 'topic' ? 'bg-purple-100 text-purple-700' : 'bg-blue-100 text-blue-700'}`}>{r.kind}</span>
                        </td>
                        <td className={`${tdClass} font-mono text-xs`}>{r.pattern}</td>
                        <td className={`${tdClass} text-center`}>
                          <button onClick={() => toggleGovRule(r.id, !r.is_active)} className="text-xs font-bold uppercase tracking-wider">
                            {r.is_active ? 'Active' : 'Inactive'}
                          </button>
                        </td>
                        <td className={`${tdClass} text-center`}>
                          <button onClick={() => deleteGovRule(r.id)} className="text-xs font-bold text-red-600 hover:underline uppercase tracking-widest">Delete</button>
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              )}
            </div>

            <div className={cardClass}>
              <div className="flex justify-between items-center mb-6">
                <h3 className="text-xs font-bold uppercase tracking-[0.3em] text-[#D4AF37]">Audit Log ({govAuditsTotal})</h3>
                <div className="flex gap-2">
                  {(['all', 'approved', 'rejected'] as const).map(f => (
                    <button key={f} onClick={() => setGovAuditFilter(f)} className={`px-3 py-1 border text-[10px] font-bold uppercase tracking-widest rounded ${govAuditFilter === f ? 'bg-[#D4AF37] text-white border-[#D4AF37]' : 'border-[#2D1B14]/20 text-[#2D1B14]/60'}`}>
                      {f}
                    </button>
                  ))}
                </div>
              </div>

              {govAudits.map(a => (
                <div key={a.id} className="flex justify-between items-center py-3 border-b border-[#2D1B14]/5 text-sm">
                  <div className="flex items-center gap-4">
                    <span>{a.approved ? 'Approved' : 'Rejected'}</span>
                    <span className="font-bold">{a.room}</span>
                    <span className="text-[#2D1B14]/60 uppercase tracking-widest text-[10px]">{a.action}</span>
                    <span className="italic">{a.topic}</span>
                  </div>
                  <div className="flex items-center gap-4">
                    {a.reasons && a.reasons.length > 0 && <span className="text-red-500 text-xs font-bold">{a.reasons.join(', ')}</span>}
                    <span className="text-[#2D1B14]/40 text-xs">{a.created_at ? new Date(a.created_at).toLocaleString() : ''}</span>
                  </div>
                </div>
              ))}
            </div>

            {/* Mass-Approve Topic Questions */}
            <div className={cardClass}>
              <h3 className="text-xs font-bold uppercase tracking-[0.3em] text-[#D4AF37] mb-6">Mass-Approve Topic Questions</h3>
              <p className="text-sm text-[#2D1B14]/60 mb-6">
                Bulk-approve all pending (unevaluated) questions that match a specific topic and optional sub-topic. This marks them as governance-approved so they can be harvested as custom facts.
              </p>

              <div className="flex gap-4 mb-4 flex-wrap">
                <div className="flex flex-col flex-1 min-w-[180px]">
                  <label className="text-[10px] font-bold uppercase tracking-widest text-[#2D1B14]/60 mb-2">Topic *</label>
                  <select
                    value={massApproveTopic}
                    onChange={e => setMassApproveTopic(e.target.value)}
                    className="p-3 border border-[#2D1B14]/20 rounded-lg bg-transparent focus:border-[#D4AF37] outline-none text-[#2D1B14] transition-colors"
                  >
                    {['History', 'Science', 'Geography', 'Literature', 'Mathematics', 'Technology', 'Art', 'Music', 'Philosophy', 'Sports'].map(t => (
                      <option key={t} value={t}>{t}</option>
                    ))}
                  </select>
                </div>
                <div className="flex flex-col flex-1 min-w-[180px]">
                  <label className="text-[10px] font-bold uppercase tracking-widest text-[#2D1B14]/60 mb-2">Sub-Topic (optional)</label>
                  <input
                    value={massApproveSubTopic}
                    onChange={e => setMassApproveSubTopic(e.target.value)}
                    placeholder="e.g. World War II, Quantum Physics..."
                    className="p-3 border border-[#2D1B14]/20 rounded-lg bg-transparent focus:border-[#D4AF37] outline-none text-[#2D1B14] transition-colors"
                  />
                </div>
                <div className="flex items-end">
                  <button
                    onClick={handleMassApproveQuestions}
                    disabled={massApproveLoading}
                    className={`px-8 py-3 text-xs font-bold uppercase tracking-widest rounded-lg transition-all duration-300 ${
                      massApproveLoading
                        ? 'bg-[#2D1B14]/30 text-white cursor-not-allowed'
                        : 'bg-[#D4AF37] text-white hover:bg-[#c29e2e] hover:shadow-lg active:scale-95'
                    }`}
                  >
                    {massApproveLoading ? (
                      <span className="flex items-center gap-2">
                        <span className="w-3 h-3 border-2 border-white border-t-transparent rounded-full animate-spin" />
                        Processing...
                      </span>
                    ) : (
                      '⚡ Mass Approve'
                    )}
                  </button>
                </div>
              </div>

              {massApproveError && (
                <div className="mt-4 p-4 bg-red-50 border border-red-200 text-red-700 rounded-lg text-sm font-bold flex items-center gap-2">
                  <span>⚠</span> {massApproveError}
                </div>
              )}
              {massApproveSuccess && (
                <div className="mt-4 p-4 bg-emerald-50 border border-emerald-200 text-emerald-700 rounded-lg text-sm font-bold flex items-center gap-2">
                  <span>✓</span> {massApproveSuccess}
                </div>
              )}
            </div>
          </div>
        )}

        {/* - INSPECTOR - */}
        {tab === 'inspector' && (
          <div className="space-y-6">
            <div className={cardClass}>
              <div className="flex gap-4 flex-wrap items-end">
                <div className="flex flex-col flex-1 min-w-[200px]">
                  <label className="text-[10px] font-bold uppercase tracking-widest text-[#2D1B14]/60 mb-2">Table</label>
                  <select
                    value={inspectorSelectedTable}
                    onChange={e => setInspectorSelectedTable(e.target.value)}
                    className="p-3 border border-[#2D1B14]/20 rounded bg-transparent focus:border-[#D4AF37] outline-none text-[#2D1B14]"
                  >
                    {inspectorTables.map(table => (
                      <option key={table.name} value={table.name}>{table.name} ({table.row_count} rows)</option>
                    ))}
                  </select>
                </div>
                
                <div className="flex flex-col w-24">
                  <label className="text-[10px] font-bold uppercase tracking-widest text-[#2D1B14]/60 mb-2">Rows</label>
                  <input
                    type="number" min={1} max={500}
                    value={inspectorLimit}
                    onChange={e => setInspectorLimit(Number(e.target.value || 100))}
                    className="p-3 border border-[#2D1B14]/20 rounded bg-transparent focus:border-[#D4AF37] outline-none text-[#2D1B14] text-center"
                  />
                </div>

                <button onClick={() => loadInspectorSchema()} className="px-4 py-3 bg-[#2D1B14] text-white text-xs font-bold uppercase tracking-widest rounded hover:bg-[#3d261c] transition-colors">Reload Schema</button>
                <button onClick={() => loadInspectorTable(inspectorSelectedTable)} className="px-4 py-3 bg-[#D4AF37] text-white text-xs font-bold uppercase tracking-widest rounded hover:bg-[#c29e2e] transition-colors">Load Table</button>
              </div>
            </div>

            {inspectorLoading && (
              <div className="p-4 bg-[#FDFCF7] border border-[#D4AF37]/30 text-[#2D1B14]/60 rounded text-sm font-bold flex items-center gap-3">
                <div className="w-4 h-4 border-2 border-[#D4AF37] border-t-transparent rounded-full animate-spin" />
                Loading table data...
              </div>
            )}

            {inspectorError && (
              <div className="p-4 bg-red-50 border border-red-200 text-red-700 rounded text-sm font-bold">
                {inspectorError}
              </div>
            )}

            {inspectorColumns.length > 0 && (
              <div className={`${cardClass} overflow-x-auto`}>
                <div className="flex items-center justify-between mb-4">
                  <div className="text-[10px] font-bold uppercase tracking-widest text-[#D4AF37]">Columns</div>
                  <div className="text-xs text-[#2D1B14]/50 font-bold">{inspectorTotalRows.toLocaleString()} total rows</div>
                </div>
                <div className="flex gap-2 flex-wrap mb-6">
                  {inspectorColumns.map(col => (
                    <span key={col.name} className="border border-[#2D1B14]/20 rounded-full px-3 py-1 text-xs text-[#2D1B14]/70">
                      <span className="font-bold text-[#2D1B14]">{col.name}</span> ({col.type})
                      {col.primary_key && <span className="text-[#D4AF37] font-bold ml-1">PK</span>}
                      {!col.nullable && <span className="text-red-500 ml-1">*</span>}
                    </span>
                  ))}
                </div>

                <div className="overflow-x-auto max-h-[600px] border border-[#2D1B14]/10">
                  <table className="w-full text-left border-collapse text-xs">
                    <thead className="bg-[#2D1B14]/5 sticky top-0">
                      <tr>
                        {inspectorColumns.map(col => (
                          <th key={col.name} className="p-3 border-b border-[#2D1B14]/10 font-bold text-[#2D1B14]/70 whitespace-nowrap">{col.name}</th>
                        ))}
                      </tr>
                    </thead>
                    <tbody>
                      {inspectorRows.map((row, idx) => (
                        <tr key={idx} className="hover:bg-[#2D1B14]/5 border-b border-[#2D1B14]/5">
                          {inspectorColumns.map(col => (
                            <td key={col.name} className="p-3 whitespace-nowrap text-[#2D1B14]/80 font-mono">
                              {renderInspectorValue(row[col.name])}
                            </td>
                          ))}
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              </div>
            )}
          </div>
        )}

        {/* - MONITORING - */}
        {tab === 'monitoring' && monitoring && (
          <div className="space-y-6">
            <div className="grid grid-cols-2 md:grid-cols-5 gap-6">
              {statCard('Total Requests', monitoring.total_requests)}
              {statCard('Total Errors', monitoring.total_errors, true)}
              {statCard('Rate Limits', monitoring.total_rate_limits, true)}
              {statCard('Recent Errors', monitoring.recent_errors_count, true)}
              {statCard('Avg Latency', `${Number(monitoring.average_latency_ms || 0).toFixed(1)}ms`)}
            </div>

            <div className={cardClass}>
              <div className="text-[10px] font-bold uppercase tracking-widest text-[#D4AF37] mb-4">Endpoint Telemetry</div>
              <div className="overflow-x-auto">
                <table className="w-full text-left border-collapse">
                  <thead>
                    <tr>
                      <th className={thClass}>Endpoint</th>
                      <th className={`${thClass} text-center`}>Requests</th>
                      <th className={`${thClass} text-center`}>Errors</th>
                      <th className={`${thClass} text-center`}>Avg ms</th>
                      <th className={`${thClass} text-center`}>P95 ms</th>
                    </tr>
                  </thead>
                  <tbody>
                    {Object.entries(monitoring.endpoint_telemetry || {}).map(([ep, meta]: [string, any]) => (
                      <tr key={ep} className="hover:bg-[#2D1B14]/5">
                        <td className={tdClass}>{ep}</td>
                        <td className={`${tdClass} text-center font-bold`}>{meta?.requests ?? 0}</td>
                        <td className={`${tdClass} text-center ${Number(meta?.errors || 0) > 0 ? 'text-red-600 font-bold' : ''}`}>{meta?.errors ?? 0}</td>
                        <td className={`${tdClass} text-center`}>{Number(meta?.avg_latency_ms || 0).toFixed(1)}</td>
                        <td className={`${tdClass} text-center`}>{Number(meta?.p95_latency_ms || 0).toFixed(1)}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </div>

            <div className="grid grid-cols-1 md:grid-cols-2 gap-6">
              <div className={cardClass}>
                <div className="text-[10px] font-bold uppercase tracking-widest text-[#D4AF37] mb-3">Recent Errors</div>
                <div className="space-y-2 max-h-64 overflow-y-auto">
                  {(monitoring.recent_errors || []).slice().reverse().map((err: any, idx: number) => (
                    <div key={`err-${idx}`} className="border border-[#2D1B14]/10 p-2 text-xs">
                      <div className="font-bold text-[#2D1B14]">{err.method} {err.endpoint}</div>
                      <div className="text-red-600">{err.status_code} - {err.error_type}</div>
                      <div className="text-[#2D1B14]/60">{err.error_message}</div>
                    </div>
                  ))}
                  {(!monitoring.recent_errors || monitoring.recent_errors.length === 0) && (
                    <div className="text-xs text-[#2D1B14]/50 italic">No recent errors.</div>
                  )}
                </div>
              </div>

              <div className={cardClass}>
                <div className="text-[10px] font-bold uppercase tracking-widest text-[#D4AF37] mb-3">Recent Requests</div>
                <div className="space-y-2 max-h-64 overflow-y-auto">
                  {(monitoring.recent_requests || []).slice().reverse().map((evt: any, idx: number) => (
                    <div key={`req-${idx}`} className="border border-[#2D1B14]/10 p-2 text-xs flex justify-between gap-3">
                      <div>
                        <div className="font-bold text-[#2D1B14]">{evt.method} {evt.endpoint}</div>
                        <div className="text-[#2D1B14]/60">{evt.status_code}</div>
                      </div>
                      <div className="font-bold text-[#D4AF37]">{Number(evt.duration_ms || 0).toFixed(1)}ms</div>
                    </div>
                  ))}
                  {(!monitoring.recent_requests || monitoring.recent_requests.length === 0) && (
                    <div className="text-xs text-[#2D1B14]/50 italic">No recent request samples.</div>
                  )}
                </div>
              </div>
            </div>
          </div>
        )}
      </div>

      {detailedSessionId && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 p-4 backdrop-blur-sm">
          <div className="w-full max-w-4xl max-h-[85vh] overflow-y-auto rounded-sm border border-[#2D1B14]/15 bg-white p-6 shadow-2xl space-y-6">
            <div className="flex justify-between items-start border-b border-gray-100 pb-4">
              <div>
                <h3 className="text-xl font-black text-[#2D1B14] font-playfair flex items-center gap-3">
                  <span>Session Log Details</span>
                  <span className={`px-2 py-0.5 text-xs font-bold uppercase tracking-wider rounded ${detailedSessionType === 'challenge' ? 'bg-red-100 text-red-600' : 'bg-blue-100 text-blue-600'}`}>
                    {detailedSessionType}
                  </span>
                </h3>
                <p className="text-xs text-[#2D1B14]/60 mt-1">Session ID: {detailedSessionId}</p>
              </div>
              <button
                onClick={() => setDetailedSessionId(null)}
                className="text-gray-400 hover:text-gray-600 font-black text-lg p-1"
              >
                ✕
              </button>
            </div>

            {detailedSessionLoading && <p className="text-[#2D1B14]/70 italic">Loading session details...</p>}
            {detailedSessionError && <p className="text-red-600 font-bold">{detailedSessionError}</p>}

            {detailedSessionData && (
              <div className="space-y-6">
                {/* 1. Header Summary */}
                <div className="grid grid-cols-2 gap-4 md:grid-cols-4 bg-[#2D1B14]/5 p-4 rounded">
                  <div>
                    <span className="block text-[10px] uppercase font-bold text-[#2D1B14]/50">Topic</span>
                    <span className="font-bold text-sm text-[#2D1B14]">{detailedSessionData.session.topic}</span>
                  </div>
                  <div>
                    <span className="block text-[10px] uppercase font-bold text-[#2D1B14]/50">Score</span>
                    <span className="font-bold text-sm text-[#2D1B14]">
                      {detailedSessionData.session.correct} / {detailedSessionData.session.questions} correct
                    </span>
                  </div>
                  <div>
                    <span className="block text-[10px] uppercase font-bold text-[#2D1B14]/50">Started At</span>
                    <span className="text-xs text-[#2D1B14]">
                      {detailedSessionData.session.started_at ? new Date(detailedSessionData.session.started_at).toLocaleString() : 'Unknown'}
                    </span>
                  </div>
                  <div>
                    <span className="block text-[10px] uppercase font-bold text-[#2D1B14]/50">Status</span>
                    <span className="font-bold text-xs text-[#2D1B14]">
                      {detailedSessionData.session.is_completed ? 'Completed' : 'Active / Pending'}
                    </span>
                  </div>
                </div>

                {/* 2. User info */}
                <div>
                  <h4 className="text-xs font-bold uppercase tracking-widest text-[#D4AF37] mb-2">Participant Information</h4>
                  <div className="overflow-x-auto">
                    <table className="w-full text-left text-xs border border-[#2D1B14]/10">
                      <thead>
                        <tr className="bg-[#2D1B14]/5">
                          <th className="p-2 font-bold">Role/User</th>
                          <th className="p-2 font-bold">Email</th>
                          <th className="p-2 font-bold">Level</th>
                          <th className="p-2 font-bold">Points</th>
                        </tr>
                      </thead>
                      <tbody>
                        {detailedSessionData.users.map((u: any) => (
                          <tr key={u.id} className="border-t border-gray-100">
                            <td className="p-2 font-semibold">
                              {u.role ? `${u.role}: ` : ''}{u.username}
                            </td>
                            <td className="p-2 text-gray-600">{u.email}</td>
                            <td className="p-2">{u.level || 'N/A'}</td>
                            <td className="p-2 font-bold">{u.points !== undefined ? u.points.toLocaleString() : 'N/A'}</td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                </div>

                {/* 3. Questions Details */}
                <div>
                  <h4 className="text-xs font-bold uppercase tracking-widest text-[#D4AF37] mb-3">Questions & Answers Log</h4>
                  <div className="space-y-4">
                    {detailedSessionData.details.map((q: any) => (
                      <div key={q.index} className="border border-[#2D1B14]/10 p-4 rounded bg-white shadow-sm space-y-3">
                        <div className="flex justify-between items-start gap-4">
                          <div className="font-bold text-sm text-[#2D1B14]">
                            {q.index}. {q.question_text}
                          </div>
                          {detailedSessionType !== 'pvp' && (
                            <span className={`px-2 py-0.5 rounded text-[10px] font-bold uppercase tracking-widest ${q.is_correct ? 'bg-green-100 text-green-700' : 'bg-red-100 text-red-700'}`}>
                              {q.is_correct ? 'Correct' : 'Incorrect'}
                            </span>
                          )}
                        </div>

                        {/* Options */}
                        <div className="grid grid-cols-1 sm:grid-cols-2 gap-2 pl-4">
                          {q.options.map((opt: string, oIdx: number) => {
                            let optionClass = "text-xs p-2 rounded border ";
                            if (opt === q.correct_answer) {
                              optionClass += "border-green-300 bg-green-50 text-green-800 font-medium";
                            } else if (detailedSessionType !== 'pvp' && opt === q.chosen_answer && !q.is_correct) {
                              optionClass += "border-red-300 bg-red-50 text-red-800 font-medium";
                            } else {
                              optionClass += "border-gray-200 text-gray-600 bg-gray-50/50";
                            }
                            return (
                              <div key={oIdx} className={optionClass}>
                                {opt}
                              </div>
                            );
                          })}
                        </div>

                        {/* PVP answers detail */}
                        {detailedSessionType === 'pvp' && q.user_answers && (
                          <div className="mt-2 pl-4 pt-2 border-t border-dashed border-gray-100 space-y-1">
                            {q.user_answers.map((ua: any, uaIdx: number) => (
                              <div key={uaIdx} className="text-xs flex justify-between items-center bg-gray-50 p-2 rounded">
                                <span className="font-semibold text-gray-700">{ua.username}</span>
                                <div className="flex items-center gap-3">
                                  <span className="text-gray-600">Answered: "{ua.chosen_answer}"</span>
                                  <span className={`px-1.5 py-0.2 rounded-[3px] text-[9px] font-bold uppercase tracking-widest ${ua.is_correct ? 'bg-green-100 text-green-700' : 'bg-red-100 text-red-700'}`}>
                                    {ua.is_correct ? 'Correct' : 'Incorrect'}
                                  </span>
                                </div>
                              </div>
                            ))}
                          </div>
                        )}

                        {/* Explanation */}
                        {q.explanation && q.explanation !== 'N/A' && (
                          <div className="text-xs text-[#2D1B14]/75 pl-4 italic bg-[#D4AF37]/5 p-2 rounded border-l-2 border-[#D4AF37]/45">
                            <span className="font-bold">Explanation:</span> {q.explanation}
                          </div>
                        )}
                      </div>
                    ))}
                  </div>
                </div>
              </div>
            )}

            <div className="flex justify-end pt-4 border-t border-gray-100">
              <button
                onClick={() => setDetailedSessionId(null)}
                className="px-4 py-2 bg-[#2D1B14] text-white text-xs font-bold uppercase tracking-widest rounded hover:bg-[#3d261c]"
              >
                Close Logs
              </button>
            </div>
          </div>
        </div>
      )}
    </InternalLayout>
  );
}
