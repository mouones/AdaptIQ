import importlib

import config


def test_room_question_knobs_can_be_overridden(monkeypatch) -> None:
    monkeypatch.setenv("QUIZ_QUESTIONS_PER_SESSION", "12")
    monkeypatch.setenv("CLASSIC_QUESTIONS_PER_SESSION", "8")
    monkeypatch.setenv("VISUAL_QUESTIONS_PER_SESSION", "9")
    monkeypatch.setenv("PVP_QUESTIONS_PER_MATCH", "6")
    monkeypatch.setenv("PVP_CANDIDATE_POOL_SIZE", "77")
    monkeypatch.setenv("CHALLENGE_POINTS_LEVEL_1", "13:-5")
    monkeypatch.setenv("CHALLENGE_STREAK_UP_THRESHOLD", "6")
    monkeypatch.setenv("CHALLENGE_RANK_A_MIN", "12345")
    monkeypatch.setenv("CHALLENGE_SESSION_QUESTION_TTL_SECONDS", "99")

    reloaded = importlib.reload(config)

    assert reloaded.QUIZ_QUESTIONS_PER_SESSION == 12
    assert reloaded.CLASSIC_QUESTIONS_PER_SESSION == 8
    assert reloaded.VISUAL_QUESTIONS_PER_SESSION == 9
    assert reloaded.PVP_QUESTIONS_PER_MATCH == 6
    assert reloaded.PVP_CANDIDATE_POOL_SIZE == 77
    assert reloaded.CHALLENGE_POINTS_LEVEL_1 == (13, -5)
    assert reloaded.CHALLENGE_STREAK_UP_THRESHOLD == 6
    assert reloaded.CHALLENGE_RANK_A_MIN == 12345
    assert reloaded.CHALLENGE_SESSION_QUESTION_TTL_SECONDS == 99

    for key in [
        "QUIZ_QUESTIONS_PER_SESSION",
        "CLASSIC_QUESTIONS_PER_SESSION",
        "VISUAL_QUESTIONS_PER_SESSION",
        "PVP_QUESTIONS_PER_MATCH",
        "PVP_CANDIDATE_POOL_SIZE",
        "CHALLENGE_POINTS_LEVEL_1",
        "CHALLENGE_STREAK_UP_THRESHOLD",
        "CHALLENGE_RANK_A_MIN",
        "CHALLENGE_SESSION_QUESTION_TTL_SECONDS",
    ]:
        monkeypatch.delenv(key, raising=False)
    importlib.reload(config)
