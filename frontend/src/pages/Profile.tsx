/** Render the Profile page flow. */

import { useEffect, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import InternalLayout from '../components/InternalLayout';
import ConceptMasterySection from '../components/profile/ConceptMasterySection';
import LearningStreakSection from '../components/profile/LearningStreakSection';
import ProfileHeader from '../components/profile/ProfileHeader';
import { API_BASE } from '../config';
import { getUserConceptMastery, ConceptMasteryItem } from '../services/customService';
import { authFetch, responseErrorMessage } from '../services/http';
import { useAuth } from '../context/AuthContext';

interface ProfileUser {
  id: string;
  email: string;
  username: string;
  points: number;
  level: string;
  is_active: boolean;
  created_at: string;
  profile_picture?: string;
}

interface ProfileStats {
  id: string;
  points: number;
  level: string;
  total_questions: number;
  global_accuracy: number;
  daily_questions: number;
  daily_accuracy: number;
  learning_time_minutes: number;
  daily_points: number;
  streak_days: number;
}

function formatMemberSince(createdAt: string): string {
  const parsed = new Date(createdAt);
  if (Number.isNaN(parsed.getTime())) {
    return 'Member since unknown';
  }

  return `Member since ${parsed.toLocaleString('en-US', { month: 'long', year: 'numeric' })}`;
}

// Render profile dashboard and load user/me plus concept mastery data.
export default function Profile() {
  const navigate = useNavigate();
  const { user: authUser, isLoading: authLoading, refreshUser } = useAuth();
  const [user, setUser] = useState<ProfileUser | null>(null);
  const [stats, setStats] = useState<ProfileStats | null>(null);
  const [concepts, setConcepts] = useState<ConceptMasteryItem[]>([]);
  const [error, setError] = useState('');
  const [loading, setLoading] = useState(true);
  const [draftUsername, setDraftUsername] = useState('');
  const [draftEmail, setDraftEmail] = useState('');
  const [currentPassword, setCurrentPassword] = useState('');
  const [newPassword, setNewPassword] = useState('');
  const [saving, setSaving] = useState(false);
  const [saveError, setSaveError] = useState('');
  const [saveMessage, setSaveMessage] = useState('');
  const [emailCode, setEmailCode] = useState('');
  const [pendingEmail, setPendingEmail] = useState('');
  const [emailVerificationRequested, setEmailVerificationRequested] = useState(false);
  const [confirmingEmail, setConfirmingEmail] = useState(false);
  const [showEditForm, setShowEditForm] = useState(false);
  const [draftAvatar, setDraftAvatar] = useState('');

  useEffect(() => {
    // Fetch profile and mastery data for the current authenticated user.
    const loadProfile = async () => {
      if (authLoading) {
        return;
      }
      if (!authUser?.id) {
        setError('You are not logged in.');
        setLoading(false);
        return;
      }

      try {
        const [meResponse, statsResponse, masteryResponse] = await Promise.all([
          authFetch(`${API_BASE}/api/auth/me`),
          authFetch(`${API_BASE}/api/auth/stats`),
          getUserConceptMastery(authUser.id).catch(() => ({ concepts: [] as ConceptMasteryItem[] })),
        ]);

        if (!meResponse.ok) {
          throw new Error(await responseErrorMessage(meResponse));
        }
        const meData = await meResponse.json().catch(() => ({}));

        if (!statsResponse.ok) {
          throw new Error(await responseErrorMessage(statsResponse));
        }
        const statsData = await statsResponse.json().catch(() => ({}));

        setUser(meData.user);
        setStats(statsData);
        setConcepts(masteryResponse.concepts || []);
      } catch (err: any) {
        setError(err.message ?? 'Failed to load profile.');
      } finally {
        setLoading(false);
      }
    };

    loadProfile();
  }, [authLoading, authUser?.id]);

  useEffect(() => {
    if (!user) return;
    setDraftUsername(user.username || '');
    setDraftAvatar(user.profile_picture || '');
    if (!pendingEmail) {
      setDraftEmail(user.email || '');
    }
  }, [user, pendingEmail]);

  const saveProfile = async () => {
    if (!user || saving) return;

    setSaveError('');
    setSaveMessage('');

    const nextUsername = draftUsername.trim();
    const nextEmail = draftEmail.trim().toLowerCase();
    const nextAvatar = draftAvatar.trim();
    const payload: Record<string, string> = {};
    const emailChanged = Boolean(nextEmail && nextEmail !== user.email.toLowerCase());

    if (nextUsername && nextUsername !== user.username) payload.username = nextUsername;
    if (nextAvatar !== (user.profile_picture || '')) payload.profile_picture = nextAvatar;
    if (currentPassword || newPassword) {
      if (!currentPassword || !newPassword) {
        setSaveError('To change password, provide both current and new password.');
        return;
      }
      payload.current_password = currentPassword;
      payload.new_password = newPassword;
    }

    if (Object.keys(payload).length === 0 && !emailChanged) {
      setSaveMessage('No changes to save.');
      return;
    }

    setSaving(true);
    try {
      let profileUpdated = false;

      if (Object.keys(payload).length > 0) {
        const response = await authFetch(`${API_BASE}/api/auth/profile`, {
          method: 'PATCH',
          body: JSON.stringify(payload),
        });
        if (!response.ok) {
          throw new Error(await responseErrorMessage(response));
        }
        const data = await response.json().catch(() => ({}));

        setUser(data);
        setCurrentPassword('');
        setNewPassword('');
        await refreshUser();
        profileUpdated = true;
      }

      if (emailChanged) {
        const response = await authFetch(`${API_BASE}/api/auth/profile/email-change/request`, {
          method: 'POST',
          body: JSON.stringify({ new_email: nextEmail }),
        });
        if (!response.ok) {
          throw new Error(await responseErrorMessage(response));
        }
        setPendingEmail(nextEmail);
        setEmailVerificationRequested(true);
        setEmailCode('');
        setDraftEmail(nextEmail);
        setSaveMessage(profileUpdated ? 'Profile updated. Verification code sent to the new email.' : 'Verification code sent to the new email.');
      } else {
        setSaveMessage('Profile updated successfully.');
      }
    } catch (err: any) {
      setSaveError(err?.message ?? 'Failed to update profile.');
    } finally {
      setSaving(false);
    }
  };

  const confirmEmailChange = async () => {
    const targetEmail = (pendingEmail || draftEmail).trim().toLowerCase();
    const code = emailCode.trim();

    if (!targetEmail || !code) {
      setSaveError('Enter the verification code sent to the new email.');
      return;
    }

    setConfirmingEmail(true);
    setSaveError('');
    setSaveMessage('');
    try {
      const response = await authFetch(`${API_BASE}/api/auth/profile/email-change/confirm`, {
        method: 'POST',
        body: JSON.stringify({ new_email: targetEmail, code }),
      });
      if (!response.ok) {
        throw new Error(await responseErrorMessage(response));
      }
      const data = await response.json().catch(() => ({}));

      setUser(data);
      setPendingEmail('');
      setEmailVerificationRequested(false);
      setEmailCode('');
      setDraftEmail(data.email || targetEmail);
      setSaveMessage('Email updated successfully.');
      await refreshUser();
    } catch (err: any) {
      setSaveError(err?.message ?? 'Failed to confirm email change.');
    } finally {
      setConfirmingEmail(false);
    }
  };

  return (
    <InternalLayout>
      <button onClick={() => navigate('/dashboard')} className="mb-10 flex items-center gap-2 text-xs font-bold uppercase tracking-widest text-[#2D1B14]/60 transition-colors hover:text-[#D4AF37]">
        Back to Dashboard
      </button>

      <div className="mx-auto max-w-7xl space-y-8">
        <div className="flex flex-col justify-between gap-4 sm:flex-row sm:items-center">
          <div>
            <h1 className="mb-3 text-4xl font-black text-[#2D1B14] font-playfair">Profile</h1>
            <p className="max-w-2xl text-sm text-[#2D1B14]/60">
              A learning archive that surfaces your current progress, streak, and concept mastery.
            </p>
          </div>
          <button
            onClick={() => {
              setShowEditForm(!showEditForm);
              setSaveError('');
              setSaveMessage('');
            }}
            className="self-start px-5 py-2.5 rounded-md bg-[#2D1B14] text-[#F5F2E7] text-xs font-bold uppercase tracking-widest hover:bg-[#3d261c] transition-all duration-300 hover:-translate-y-0.5 shadow-sm hover:shadow-md"
          >
            {showEditForm ? 'View Profile Dashboard' : 'Edit Profile Parameters'}
          </button>
        </div>

        {loading && (
          <div className="rounded-lg border border-[#2D1B14]/8 bg-white p-6 text-[#2D1B14] animate-pulse">Loading profile...</div>
        )}

        {!loading && error && (
          <div className="rounded-lg border border-red-200 bg-red-50 p-6 text-red-700">{error}</div>
        )}

        {!loading && user && (
          <div className="space-y-8">
            <ProfileHeader
              username={user.username}
              email={user.email}
              level={user.level}
              points={user.points}
              memberSince={formatMemberSince(user.created_at)}
              profilePicture={user.profile_picture}
            />

            {showEditForm ? (
              <div className="rounded-lg border border-[#2D1B14]/8 bg-white p-8 shadow-sm">
                <div className="mb-4">
                  <h2 className="text-lg font-black text-[#2D1B14] font-playfair">Edit Profile</h2>
                  <p className="text-xs text-[#2D1B14]/60">Update your username, email, password, or avatar.</p>
                </div>

                <div className="grid grid-cols-1 gap-5 md:grid-cols-2">
                  <div className="flex flex-col gap-1 md:col-span-2">
                    <label className="text-xs font-bold uppercase tracking-widest text-[#2D1B14]/60">Profile Picture / Avatar</label>
                    <div className="mt-2 flex flex-col gap-4 sm:flex-row sm:items-center">
                      <div className="h-16 w-16 overflow-hidden rounded-full border-2 border-[#D4AF37] bg-white flex items-center justify-center shadow-md">
                        <img
                          src={draftAvatar || `https://api.dicebear.com/7.x/bottts/svg?seed=${encodeURIComponent(draftUsername || 'user')}`}
                          alt="Avatar Preview"
                          className="h-full w-full object-cover"
                        />
                      </div>
                      <div className="flex-1 space-y-2">
                        <div className="flex flex-wrap gap-2">
                          {['bottts', 'adventurer', 'lorelei', 'fun-emoji', 'avataaars', 'pixel-art'].map(style => (
                            <button
                              key={style}
                              type="button"
                              onClick={() => {
                                const newAvatar = `https://api.dicebear.com/7.x/${style}/svg?seed=${encodeURIComponent(draftUsername || 'seed')}`;
                                setDraftAvatar(newAvatar);
                              }}
                              className={`px-3 py-1 text-xs rounded border transition-colors ${draftAvatar.includes(`/${style}/`) ? 'border-[#D4AF37] bg-[#D4AF37]/10 text-[#2D1B14]' : 'border-gray-200 hover:border-gray-400'}`}
                            >
                              {style}
                            </button>
                          ))}
                        </div>
                        <input
                          value={draftAvatar}
                          onChange={(e) => setDraftAvatar(e.target.value)}
                          placeholder="Or paste any custom image URL here"
                          className="w-full border border-[#2D1B14]/20 px-3 py-2 text-sm outline-none focus:border-[#D4AF37]"
                        />
                      </div>
                    </div>
                  </div>

                  <div className="flex flex-col gap-1">
                    <label className="text-xs font-bold uppercase tracking-widest text-[#2D1B14]/60">Username</label>
                    <input
                      value={draftUsername}
                      onChange={(e) => setDraftUsername(e.target.value)}
                      className="rounded-md border border-[#2D1B14]/15 px-3 py-2.5 text-sm outline-none focus:border-[#D4AF37] transition-colors bg-[#FDFCF7]"
                      placeholder="Username"
                    />
                  </div>
                  <div className="flex flex-col gap-1">
                    <label className="text-xs font-bold uppercase tracking-widest text-[#2D1B14]/60">Email</label>
                    <input
                      value={draftEmail}
                      onChange={(e) => {
                        setDraftEmail(e.target.value);
                        if (pendingEmail && e.target.value.trim().toLowerCase() !== pendingEmail) {
                          setPendingEmail('');
                          setEmailVerificationRequested(false);
                          setEmailCode('');
                        }
                      }}
                      className="border border-[#2D1B14]/20 px-3 py-2 text-sm outline-none focus:border-[#D4AF37]"
                      placeholder="Email"
                      type="email"
                    />
                  </div>
                  <div className="flex flex-col gap-1">
                    <label className="text-xs font-bold uppercase tracking-widest text-[#2D1B14]/60">Current Password</label>
                    <input
                      value={currentPassword}
                      onChange={(e) => setCurrentPassword(e.target.value)}
                      className="border border-[#2D1B14]/20 px-3 py-2 text-sm outline-none focus:border-[#D4AF37]"
                      type="password"
                      placeholder="Only if changing password"
                    />
                  </div>
                  <div className="flex flex-col gap-1">
                    <label className="text-xs font-bold uppercase tracking-widest text-[#2D1B14]/60">New Password</label>
                    <input
                      value={newPassword}
                      onChange={(e) => setNewPassword(e.target.value)}
                      className="border border-[#2D1B14]/20 px-3 py-2 text-sm outline-none focus:border-[#D4AF37]"
                      type="password"
                      placeholder="Minimum 8 characters"
                    />
                  </div>
                </div>

                {emailVerificationRequested && (
                  <div className="mt-4 border border-[#D4AF37]/40 bg-[#FDFCF7] p-4">
                    <div className="mb-3 text-xs font-semibold text-[#2D1B14]/70">
                      Verification code sent to {pendingEmail}.
                    </div>
                    <div className="flex flex-col gap-3 md:flex-row md:items-end">
                      <div className="flex flex-1 flex-col gap-1">
                        <label className="text-xs font-bold uppercase tracking-widest text-[#2D1B14]/60">Verification Code</label>
                        <input
                          value={emailCode}
                          onChange={(e) => setEmailCode(e.target.value)}
                          className="border border-[#2D1B14]/20 px-3 py-2 text-sm outline-none focus:border-[#D4AF37]"
                          placeholder="Verification code"
                          inputMode="numeric"
                        />
                      </div>
                      <button
                        onClick={confirmEmailChange}
                        disabled={confirmingEmail}
                        className="bg-[#D4AF37] px-4 py-2 text-xs font-bold uppercase tracking-widest text-[#2D1B14] transition-colors hover:bg-[#c39f2c] disabled:opacity-50"
                      >
                        {confirmingEmail ? 'Confirming...' : 'Confirm Email'}
                      </button>
                    </div>
                  </div>
                )}

                {saveError && (
                  <div className="mt-4 border border-red-200 bg-red-50 px-3 py-2 text-sm text-red-700">{saveError}</div>
                )}
                {saveMessage && (
                  <div className="mt-4 border border-green-200 bg-green-50 px-3 py-2 text-sm text-green-700">{saveMessage}</div>
                )}

                <div className="mt-4 flex gap-3">
                  <button
                    onClick={saveProfile}
                    disabled={saving}
                    className="rounded-md bg-[#2D1B14] px-5 py-2.5 text-xs font-bold uppercase tracking-widest text-[#F5F2E7] transition-all hover:bg-[#3d261c] disabled:opacity-50 hover:shadow-md"
                  >
                    {saving ? 'Saving...' : 'Save Changes'}
                  </button>
                  <button
                    type="button"
                    onClick={() => setShowEditForm(false)}
                    className="rounded-md border border-[#2D1B14]/15 px-5 py-2.5 text-xs font-bold uppercase tracking-widest text-[#2D1B14] transition-all hover:bg-[#FDFCF7]"
                  >
                    Back to Dashboard
                  </button>
                </div>
              </div>
            ) : (
              <>
                <div className="grid grid-cols-1 gap-4 md:grid-cols-4">
                  <div className="rounded-lg border border-[#2D1B14]/8 bg-white p-6 shadow-sm hover:shadow-md transition-shadow">
                    <div className="mb-1 text-xs font-bold uppercase tracking-widest text-[#2D1B14]/55">Level</div>
                    <div className="text-2xl font-black text-[#2D1B14] font-playfair">{user.level}</div>
                  </div>
                  <div className="rounded-lg border border-[#2D1B14]/8 bg-white p-6 shadow-sm hover:shadow-md transition-shadow">
                    <div className="mb-1 text-xs font-bold uppercase tracking-widest text-[#2D1B14]/55">Points</div>
                    <div className="text-2xl font-black text-[#D4AF37] font-playfair">{user.points.toLocaleString()}</div>
                  </div>
                  <div className="rounded-lg border border-[#2D1B14]/8 bg-white p-6 shadow-sm hover:shadow-md transition-shadow">
                    <div className="mb-1 text-xs font-bold uppercase tracking-widest text-[#2D1B14]/55">Member Since</div>
                    <div className="text-sm font-semibold text-[#2D1B14]">{formatMemberSince(user.created_at)}</div>
                  </div>
                  <div className="rounded-lg border border-[#2D1B14]/8 bg-white p-6 shadow-sm hover:shadow-md transition-shadow">
                    <div className="mb-1 text-xs font-bold uppercase tracking-widest text-[#2D1B14]/55">Status</div>
                    <div className="text-sm font-semibold text-[#2D1B14]">{user.is_active ? 'Active learner' : 'Inactive'}</div>
                  </div>
                </div>

                {stats && (
                  <LearningStreakSection
                    streakDays={stats.streak_days}
                    dailyQuestions={stats.daily_questions}
                    dailyAccuracy={stats.daily_accuracy}
                    dailyPoints={stats.daily_points}
                    learningTimeMinutes={stats.learning_time_minutes}
                  />
                )}

                <ConceptMasterySection concepts={concepts} />
              </>
            )}
          </div>
        )}
      </div>
    </InternalLayout>
  );
}
