/**
 * pages/PvPRoom.tsx — PvP 1v1 matchmaking and quiz room.
 *
 * Fixed behavior:
 *   - No fake "Next Question" after 5/5
 *   - Shows waiting state after user finishes all questions
 *   - Polls live Redis-backed match state
 *   - Supports explicit "Leave Match" / forfeit
 *   - Refreshes rating and leaderboard after result
 */

import { useState, useEffect, useRef, useCallback } from 'react';
import { useNavigate } from 'react-router-dom';
import { useAuth } from '../context/AuthContext';
import InternalLayout from '../components/InternalLayout';
import {
  joinQueue,
  leaveQueue,
  getQueueStatus,
  getMatch,
  getPvPMatchState,
  submitPvPAnswer,
  endPvPMatch,
  forfeitPvPMatch,
  getPvPRating,
  getLeaderboard,
  type PvPMatchData,
  type PvPQuestion,
  type SubmitAnswerResponse,
  type EndMatchResponse,
  type PvPRating,
  type LeaderboardEntry,
} from '../services/pvpService';

type Phase = 'lobby' | 'searching' | 'playing' | 'results';

const PVP_QUESTION_SECONDS = 20;

export default function PvPRoom() {
  const { user } = useAuth();
  const navigate = useNavigate();

  // Main UI state
  const [phase, setPhase] = useState<Phase>('lobby');
  const [topic, setTopic] = useState('Mixed');
  const [error, setError] = useState('');

  // Matchmaking
  const [opponentName, setOpponentName] = useState('Opponent');
  const queuePollRef = useRef<ReturnType<typeof setInterval> | null>(null);

  // Match
  const [match, setMatch] = useState<PvPMatchData | null>(null);
  const [currentIndex, setCurrentIndex] = useState(0);
  const [selectedAnswer, setSelectedAnswer] = useState<string | null>(null);
  const [feedback, setFeedback] = useState<SubmitAnswerResponse | null>(null);
  const [pvpTimeLeft, setPvpTimeLeft] = useState(PVP_QUESTION_SECONDS);
  const [myScore, setMyScore] = useState(0);
  const [oppScore, setOppScore] = useState(0);
  const [isEnding, setIsEnding] = useState(false);
  const [isLeaving, setIsLeaving] = useState(false);
  const endingRef = useRef(false);
  const startTime = useRef(Date.now());

  // Results
  const [results, setResults] = useState<EndMatchResponse | null>(null);

  // Rating / leaderboard
  const [rating, setRating] = useState<PvPRating | null>(null);
  const [leaderboard, setLeaderboard] = useState<LeaderboardEntry[]>([]);

  const currentQ: PvPQuestion | null = match?.questions?.[0] ?? null;

  const isUser1 = Boolean(user && match && user.id === match.user1_id);

  useEffect(() => {
    setPvpTimeLeft(PVP_QUESTION_SECONDS);
  }, [currentQ?.id]);

  useEffect(() => {
    if (phase !== 'playing' || !currentQ || selectedAnswer || feedback || isEnding) return;
    if (pvpTimeLeft <= 0) {
      void handleAnswer('__timeout__');
      return;
    }
    const timer = setInterval(() => setPvpTimeLeft((prev) => prev - 1), 1000);
    return () => clearInterval(timer);
  }, [phase, currentQ?.id, selectedAnswer, feedback, isEnding, pvpTimeLeft]);

  const refreshRatingAndLeaderboard = useCallback(async () => {
    if (!user) return;

    try {
      const nextRating = await getPvPRating(user.id);
      setRating(nextRating);
    } catch {
      // keep old rating
    }

    try {
      const nextBoard = await getLeaderboard(10);
      setLeaderboard(nextBoard.entries);
    } catch {
      // keep old leaderboard
    }
  }, [user]);

  const clearQueuePoll = () => {
    if (queuePollRef.current) {
      clearInterval(queuePollRef.current);
      queuePollRef.current = null;
    }
  };

  const resetLocalMatchState = () => {
    setMatch(null);
    setCurrentIndex(0);
    setSelectedAnswer(null);
    setFeedback(null);
    setPvpTimeLeft(PVP_QUESTION_SECONDS);
    setResults(null);
    setMyScore(0);
    setOppScore(0);
    setError('');
    setIsEnding(false);
    setIsLeaving(false);
    endingRef.current = false;
  };

  useEffect(() => {
    refreshRatingAndLeaderboard();

    return () => {
      clearQueuePoll();
    };
  }, [refreshRatingAndLeaderboard]);

  // ── Finish match safely ────────────────────────────────────────────────

  const finishMatch = useCallback(
    async (matchId: string) => {
      if (endingRef.current) return;

      endingRef.current = true;
      setIsEnding(true);
      setError('');

      try {
        const endResult = await endPvPMatch(matchId);
        setResults(endResult);
        setPhase('results');
        await refreshRatingAndLeaderboard();
      } catch (e: any) {
        setError(e?.message || 'Failed to finalize match');
      } finally {
        setIsEnding(false);
      }
    },
    [refreshRatingAndLeaderboard],
  );

  // ── Poll active match state ────────────────────────────────────────────

  useEffect(() => {
    if (phase !== 'playing' || !match) return;

    const timer = setInterval(async () => {
      try {
        const state = await getPvPMatchState(match.match_id);

        const nextMyScore = isUser1 ? state.user1_score : state.user2_score;
        const nextOppScore = isUser1 ? state.user2_score : state.user1_score;

        setMyScore(nextMyScore);
        setOppScore(nextOppScore);

        setMatch((prev) =>
          prev
            ? {
                ...prev,
                status: state.status,
                user1_score: state.user1_score,
                user2_score: state.user2_score,
                user1_finished: state.user1_finished,
                user2_finished: state.user2_finished,
              }
            : prev,
        );

        if (state.status === 'completed') {
          await finishMatch(match.match_id);
        }
      } catch {
        // Keep polling. Temporary network/API errors should not kill the page.
      }
    }, 1500);

    return () => clearInterval(timer);
  }, [phase, match?.match_id, isUser1, finishMatch]);

  // ── Join matchmaking queue ─────────────────────────────────────────────

  const handleJoinQueue = async () => {
    setError('');
    resetLocalMatchState();

    try {
      await joinQueue(topic);
      setPhase('searching');

      clearQueuePoll();

      queuePollRef.current = setInterval(async () => {
        try {
          const status = await getQueueStatus();

          if (status.status === 'matched' && status.match_id) {
            clearQueuePoll();

            setOpponentName(status.opponent_username || 'Opponent');

            const matchData = await getMatch(status.match_id);

            setMatch(matchData);
            setCurrentIndex(matchData.questions[0]?.index ?? 0);
            setMyScore(user?.id === matchData.user1_id ? matchData.user1_score : matchData.user2_score);
            setOppScore(user?.id === matchData.user1_id ? matchData.user2_score : matchData.user1_score);
            setSelectedAnswer(null);
            setFeedback(null);
            setPvpTimeLeft(PVP_QUESTION_SECONDS);
            setPhase('playing');

            startTime.current = Date.now();
          }

          if (status.status === 'expired' || status.status === 'not_in_queue') {
            clearQueuePoll();
            setPhase('lobby');
            setError(status.message || 'Matchmaking expired. Try again.');
          }
        } catch {
          // keep polling
        }
      }, 2500);
    } catch (e: any) {
      setError(e?.message || 'Failed to join queue');
      setPhase('lobby');
    }
  };

  const handleLeaveQueue = async () => {
    clearQueuePoll();

    try {
      await leaveQueue();
    } catch {
      // ignore
    }

    setPhase('lobby');
  };

  // ── Forfeit active match ───────────────────────────────────────────────

  const handleForfeit = async () => {
    if (!match || isLeaving) return;

    const confirmed = window.confirm(
      'Leave this match? This will count as a loss and your opponent will win.',
    );

    if (!confirmed) return;

    setIsLeaving(true);
    setError('');

    try {
      const result = await forfeitPvPMatch(match.match_id);
      setResults(result);
      setPhase('results');
      await refreshRatingAndLeaderboard();
    } catch (e: any) {
      setError(e?.message || 'Failed to leave match');
    } finally {
      setIsLeaving(false);
    }
  };

  // ── Submit answer ──────────────────────────────────────────────────────

  const handleAnswer = async (answer: string) => {
    if (!match || selectedAnswer || isEnding) return;

    const q = match.questions[0];

    if (!q) {
      setError('No playable question is available. Please wait for sync.');
      return;
    }

    setSelectedAnswer(answer);
    setError('');

    const timeTaken = Math.min(PVP_QUESTION_SECONDS, (Date.now() - startTime.current) / 1000);

    try {
      const result = await submitPvPAnswer(
        match.match_id,
        q.id,
        q.index,
        answer,
        timeTaken,
      );

      setFeedback(result);
      setMyScore(result.your_score);
      setOppScore(result.opponent_score);

      if (result.match_finished) {
        setTimeout(() => {
          finishMatch(match.match_id);
        }, 900);
      }
    } catch (e: any) {
      setError(e?.message || 'Failed to submit answer');
      setSelectedAnswer(null);
    }
  };

  // ── Next question ──────────────────────────────────────────────────────

  const handleNextQuestion = () => {
    if (!match || !feedback?.next_question) return;

    const next = feedback.next_question;

    setMatch((prev) =>
      prev
        ? {
            ...prev,
            questions: [next as PvPQuestion],
          }
        : prev,
    );

    setCurrentIndex(next.index);
    setSelectedAnswer(null);
    setFeedback(null);
    setError('');
    setPvpTimeLeft(PVP_QUESTION_SECONDS);

    startTime.current = Date.now();
  };

  // ── Render helpers ─────────────────────────────────────────────────────

  const renderScoreBar = () => (
    <div className="flex justify-between items-center bg-white rounded-lg border border-[#2D1B14]/8 p-6 shadow-sm">
      <div className="text-center min-w-[120px]">
        <div className="text-[10px] font-bold uppercase tracking-widest text-[#D4AF37] mb-1">
          You
        </div>
        <div className="text-2xl font-black font-playfair text-[#2D1B14]">
          {myScore}
        </div>
      </div>

      <div className="text-center">
        <div className="text-[10px] font-bold uppercase tracking-widest text-[#2D1B14]/40 mb-1">
          Question
        </div>
        <div className="text-xl font-black font-playfair text-[#2D1B14]">
          {Math.min(currentIndex + 1, match?.total_questions || 1)}
          <span className="text-lg text-[#2D1B14]/40">
            {' '}
            / {match?.total_questions || 5}
          </span>
        </div>
      </div>

      <div className="text-center min-w-[90px]">
        <div className="text-[10px] font-bold uppercase tracking-widest text-[#2D1B14]/40 mb-1">
          Time
        </div>
        <div className={`text-xl font-black font-mono ${pvpTimeLeft <= 5 && !selectedAnswer ? 'text-red-500 animate-pulse' : 'text-[#2D1B14]'}`}>
          {selectedAnswer ? '—' : `${pvpTimeLeft}s`}
        </div>
      </div>

      <div className="text-center min-w-[120px]">
        <div className="text-[10px] font-bold uppercase tracking-widest text-[#D4AF37] mb-1">
          {opponentName}
        </div>
        <div className="text-2xl font-black font-playfair text-[#2D1B14]">
          {oppScore}
        </div>
      </div>
    </div>
  );

  // ── Main render ────────────────────────────────────────────────────────

  return (
    <InternalLayout>
      <div className="max-w-7xl mx-auto p-8">
        <div className="flex items-center justify-between gap-4 mb-8">
          <h1 className="text-4xl font-black font-playfair text-[#2D1B14]">
            PvP Arena
          </h1>

          {phase === 'playing' && (
            <button
              onClick={handleForfeit}
              disabled={isLeaving || isEnding}
              className="px-5 py-3 rounded-md border border-red-200 text-red-600 text-xs font-bold uppercase tracking-widest hover:bg-red-50 disabled:opacity-50 disabled:cursor-not-allowed"
            >
              {isLeaving ? 'Leaving...' : 'Leave Match'}
            </button>
          )}
        </div>

        {error && (
          <div className="bg-red-50 border border-red-200 text-red-700 px-4 py-3 rounded mb-6 font-bold text-sm">
            {error}
          </div>
        )}

        {/* ── LOBBY ── */}
        {phase === 'lobby' && (
          <div className="space-y-8">
            {rating && (
              <div className="bg-white p-8 rounded-lg border border-[#2D1B14]/8 shadow-sm flex flex-col md:flex-row justify-between items-center gap-6 hover:shadow-md transition-shadow">
                <div>
                  <div className="text-[10px] font-bold uppercase tracking-widest text-[#D4AF37] mb-2">
                    Your Elo Rating
                  </div>
                  <div className="text-4xl font-black font-playfair text-[#2D1B14]">
                    {Math.round(rating.elo_rating)}
                  </div>
                </div>

                <div className="flex gap-8 text-center">
                  <div>
                    <div className="text-2xl font-black font-playfair text-[#2D1B14]">
                      {rating.total_wins}
                    </div>
                    <div className="text-[10px] font-bold uppercase tracking-widest text-[#2D1B14]/40">
                      Wins
                    </div>
                  </div>

                  <div>
                    <div className="text-2xl font-black font-playfair text-[#2D1B14]">
                      {rating.total_losses}
                    </div>
                    <div className="text-[10px] font-bold uppercase tracking-widest text-[#2D1B14]/40">
                      Losses
                    </div>
                  </div>

                  <div>
                    <div className="text-2xl font-black font-playfair text-[#2D1B14]">
                      {rating.total_draws}
                    </div>
                    <div className="text-[10px] font-bold uppercase tracking-widest text-[#2D1B14]/40">
                      Draws
                    </div>
                  </div>

                  <div>
                    <div className="text-2xl font-black font-playfair text-[#D4AF37]">
                      {Math.round(rating.win_rate)}%
                    </div>
                    <div className="text-[10px] font-bold uppercase tracking-widest text-[#2D1B14]/40">
                      Win Rate
                    </div>
                  </div>
                </div>
              </div>
            )}

            <div className="bg-white p-8 rounded-lg border border-[#2D1B14]/8 shadow-sm">
              <label className="block text-[10px] font-bold uppercase tracking-widest text-[#D4AF37] mb-4">
                Select Topic
              </label>

              <div className="flex flex-col md:flex-row gap-4 mb-8">
                {['Mixed', 'History', 'Geography'].map((t) => (
                  <button
                    key={t}
                    onClick={() => setTopic(t)}
                    className={`flex-1 py-4 rounded-md text-xs font-bold uppercase tracking-widest border transition-all duration-200 ${
                      topic === t
                        ? 'bg-[#2D1B14] text-[#F5F2E7] border-[#2D1B14] shadow-md'
                        : 'bg-transparent text-[#2D1B14]/60 border-[#2D1B14]/20 hover:border-[#D4AF37]'
                    }`}
                  >
                    {t}
                  </button>
                ))}
              </div>

              <button
                onClick={handleJoinQueue}
                className="w-full py-4 rounded-md bg-[#D4AF37] text-white text-sm font-bold uppercase tracking-[0.2em] shadow-lg hover:bg-[#c29e2e] transition-colors"
              >
                Find Opponent
              </button>
            </div>

            {leaderboard.length > 0 && (
              <div className="bg-white p-8 rounded-lg border border-[#2D1B14]/8 shadow-sm hover:shadow-md transition-shadow">
                <h3 className="text-xs font-bold uppercase tracking-[0.3em] text-[#D4AF37] mb-6">
                  Leaderboard
                </h3>

                <div className="space-y-4">
                  {leaderboard.map((entry) => (
                    <div
                      key={entry.user_id}
                      className="flex justify-between items-center py-3 border-b border-[#2D1B14]/5 last:border-0"
                    >
                      <div className="flex items-center gap-4">
                        <span className="text-lg font-black font-playfair text-[#2D1B14]/40 w-8">
                          #{entry.rank}
                        </span>
                        <span className="font-bold text-[#2D1B14]">
                          {entry.username}
                        </span>
                      </div>

                      <div className="text-right">
                        <div className="text-lg font-black font-playfair text-[#D4AF37]">
                          {Math.round(entry.elo_rating)} Elo
                        </div>
                        <div className="text-[10px] font-bold uppercase tracking-widest text-[#2D1B14]/40">
                          {entry.total_wins} Wins ({Math.round(entry.win_rate)}%)
                        </div>
                      </div>
                    </div>
                  ))}
                </div>
              </div>
            )}
          </div>
        )}

        {/* ── SEARCHING ── */}
        {phase === 'searching' && (
          <div className="text-center py-24 bg-white rounded-lg border border-[#2D1B14]/8 shadow-sm">
            <div className="w-16 h-16 mx-auto mb-8 border-4 border-[#2D1B14]/10 border-t-[#D4AF37] rounded-full animate-spin" />

            <h2 className="text-2xl font-black font-playfair text-[#2D1B14] mb-2">
              Searching for opponent...
            </h2>

            <p className="text-[#2D1B14]/60 mb-8 uppercase tracking-widest text-xs font-bold">
              Topic: {topic}
            </p>

            <button
              onClick={handleLeaveQueue}
              className="px-8 py-3 rounded-md bg-transparent border border-[#2D1B14]/20 text-[#2D1B14]/60 text-xs font-bold uppercase tracking-widest hover:border-[#D4AF37] hover:text-[#D4AF37] transition-colors"
            >
              Cancel Matchmaking
            </button>
          </div>
        )}

        {/* ── PLAYING WITH QUESTION ── */}
        {phase === 'playing' && currentQ && (
          <div className="space-y-6">
            {renderScoreBar()}

            <div className="bg-[#FDFCF7] rounded-lg border border-[#2D1B14]/8 p-10 shadow-sm">
              <p className="text-xl leading-relaxed text-[#2D1B14] font-medium text-center">
                {currentQ.text}
              </p>
            </div>

            <div className="grid grid-cols-1 gap-4">
              {currentQ.options.map((opt, index) => {
                let bgClass = 'bg-white hover:border-[#D4AF37]';
                let borderClass = 'border-[#2D1B14]/10';
                let textClass = 'text-[#2D1B14]';

                if (feedback && selectedAnswer) {
                  if (opt.trim() === feedback.correct_answer.trim()) {
                    bgClass = 'bg-green-50';
                    borderClass = 'border-green-500';
                    textClass = 'text-green-800 font-bold';
                  } else if (opt === selectedAnswer && !feedback.is_correct) {
                    bgClass = 'bg-red-50';
                    borderClass = 'border-red-500';
                    textClass = 'text-red-800';
                  }
                }

                return (
                  <button
                    key={`${currentQ.id}-${index}`}
                    onClick={() => handleAnswer(opt)}
                    disabled={Boolean(selectedAnswer) || isEnding}
                    className={`p-6 rounded-lg text-left border shadow-sm transition-all duration-200 text-lg ${bgClass} ${borderClass} ${textClass} ${
                      selectedAnswer
                        ? 'cursor-default'
                        : 'cursor-pointer hover:shadow-md hover:-translate-y-0.5'
                    }`}
                  >
                    {opt}
                  </button>
                );
              })}
            </div>

            {feedback && (
              <div className="mt-8 bg-white p-8 rounded-lg border border-[#2D1B14]/8 shadow-sm">
                <div
                  className={`text-sm font-black uppercase tracking-[0.2em] mb-4 ${
                    feedback.is_correct ? 'text-green-600' : 'text-red-600'
                  }`}
                >
                  {feedback.is_correct ? 'Correct Answer' : 'Wrong Answer'}
                </div>

                {feedback.explanation && (
                  <p className="text-[#2D1B14]/80 text-sm leading-relaxed mb-6 italic border-l-4 border-[#D4AF37] pl-4">
                    {feedback.explanation}
                  </p>
                )}

                {feedback.next_question && !feedback.match_finished && (
                  <button
                    onClick={handleNextQuestion}
                    className="w-full py-4 rounded-md bg-[#2D1B14] text-[#F5F2E7] text-xs font-bold uppercase tracking-[0.2em] shadow-lg hover:bg-[#3d261c] transition-colors"
                  >
                    Next Question
                  </button>
                )}

                {!feedback.next_question && !feedback.match_finished && (
                  <div className="text-center py-5 rounded-md bg-[#FDFCF7] border border-[#2D1B14]/10">
                    <div className="text-xs font-bold uppercase tracking-[0.2em] text-[#D4AF37] mb-2">
                      Waiting for opponent
                    </div>
                    <p className="text-sm text-[#2D1B14]/60">
                      You finished your questions. The match will end when your opponent finishes or leaves.
                    </p>
                  </div>
                )}

                {feedback.match_finished && (
                  <div className="text-center py-5 rounded-md bg-[#FDFCF7] border border-[#2D1B14]/10">
                    <div className="text-xs font-bold uppercase tracking-[0.2em] text-[#D4AF37] mb-2">
                      Finalizing match
                    </div>
                    <p className="text-sm text-[#2D1B14]/60">
                      Calculating result and Elo update...
                    </p>
                  </div>
                )}
              </div>
            )}
          </div>
        )}

        {/* ── PLAYING BUT NO QUESTION, WAITING ── */}
        {phase === 'playing' && !currentQ && match && (
          <div className="space-y-6">
            {renderScoreBar()}

            <div className="text-center py-24 bg-white rounded-lg border border-[#2D1B14]/8 shadow-sm">
              <div className="w-14 h-14 mx-auto mb-8 border-4 border-[#2D1B14]/10 border-t-[#D4AF37] rounded-full animate-spin" />

              <h2 className="text-2xl font-black font-playfair text-[#2D1B14] mb-3">
                Waiting for opponent
              </h2>

              <p className="text-[#2D1B14]/60 mb-8 text-sm">
                You already answered all questions. The result will appear when your opponent finishes or leaves.
              </p>

              <button
                onClick={handleForfeit}
                disabled={isLeaving || isEnding}
                className="px-8 py-3 rounded-md border border-red-200 text-red-600 text-xs font-bold uppercase tracking-widest hover:bg-red-50 disabled:opacity-50"
              >
                {isLeaving ? 'Leaving...' : 'Leave Match'}
              </button>
            </div>
          </div>
        )}

        {/* ── RESULTS ── */}
        {phase === 'results' && (
          <div className="text-center py-16 bg-white rounded-lg border border-[#2D1B14]/8 shadow-sm">
            <div className="text-6xl mb-6">
              {results?.result === 'win'
                ? '🏆'
                : results?.result === 'loss'
                  ? '😞'
                  : '🤝'}
            </div>

            <h2 className="text-4xl font-black font-playfair text-[#2D1B14] mb-8">
              {results?.result === 'win'
                ? 'Victory!'
                : results?.result === 'loss'
                  ? 'Defeat'
                  : 'Draw'}
            </h2>

            <div className="flex justify-center items-center gap-12 mb-12">
              <div className="text-center">
                <div className="text-[10px] font-bold uppercase tracking-widest text-[#D4AF37] mb-2">
                  You
                </div>
                <div className="text-5xl font-black font-playfair text-[#2D1B14]">
                  {results?.your_score ?? myScore}
                </div>
              </div>

              <div className="text-3xl font-black font-playfair text-[#2D1B14]/20">
                VS
              </div>

              <div className="text-center">
                <div className="text-[10px] font-bold uppercase tracking-widest text-[#D4AF37] mb-2">
                  {results?.opponent_username || opponentName || 'Opponent'}
                </div>
                <div className="text-5xl font-black font-playfair text-[#2D1B14]">
                  {results?.opponent_score ?? oppScore}
                </div>
              </div>
            </div>

            {results && (
              <div className="mb-12 inline-block bg-[#FDFCF7] rounded-lg border border-[#2D1B14]/8 px-8 py-4 shadow-inner">
                <div className="text-[10px] font-bold uppercase tracking-widest text-[#2D1B14]/60 mb-2">
                  Elo Rating Update
                </div>

                <div className="text-2xl font-black font-playfair text-[#2D1B14]">
                  {Math.round(results.new_elo - results.elo_change)}
                  <span className="text-[#D4AF37]"> → </span>
                  {Math.round(results.new_elo)}
                </div>

                <div
                  className={`text-xs font-bold mt-1 ${
                    results.elo_change > 0
                      ? 'text-green-600'
                      : results.elo_change < 0
                        ? 'text-red-600'
                        : 'text-[#2D1B14]/60'
                  }`}
                >
                  {results.elo_change > 0 ? '+' : ''}
                  {results.elo_change.toFixed(1)} Points
                </div>
              </div>
            )}

            <div className="flex justify-center gap-4">
              <button
                onClick={() => {
                  resetLocalMatchState();
                  setPhase('lobby');
                  refreshRatingAndLeaderboard();
                }}
                className="px-8 py-4 rounded-md bg-[#D4AF37] text-white text-xs font-bold uppercase tracking-[0.2em] shadow-lg hover:bg-[#c29e2e] transition-colors"
              >
                Play Again
              </button>

              <button
                onClick={() => navigate('/dashboard')}
                className="px-8 py-4 rounded-md bg-transparent border border-[#2D1B14]/20 text-[#2D1B14]/60 text-xs font-bold uppercase tracking-[0.2em] hover:border-[#D4AF37] hover:text-[#D4AF37] transition-colors"
              >
                Return to Dashboard
              </button>
            </div>
          </div>
        )}
      </div>
    </InternalLayout>
  );
}