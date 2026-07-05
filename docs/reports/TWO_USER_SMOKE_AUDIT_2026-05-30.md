# Two-User Smoke Audit (2026-05-30)

## Run Context
- Source run: Postman/Newman full collection (`83` requests, `116` assertions, `0` failures)
- Backend base URL: `http://127.0.0.1:8000`
- Synthetic users created by collection pre-request script:
  - `user1_1780147731123@example.com` (`5e9eee47-a0b7-4ebc-897b-055396916018`)
  - `user2_1780147731123@example.com` (`a9c01983-c004-4732-b036-06f7cc03f776`)

## User State
- `user1`:
  - `is_admin=true` (became admin after bootstrap step in collection)
  - `is_active=true`
  - `ban_until=null`
- `user2`:
  - `is_admin=false`
  - `is_active=true`
  - `ban_until=null`

## Room-by-Room Audit

### Classic Room
- Session table: `classic_sessions`
- Session id: `303744eb-e8c4-48c1-b52b-ee00f69c26b8`
- User: `user1`
- Topic: `history`
- Totals: `questions_answered=1`, `correct_count=0`
- Response persistence table: `user_responses`
  - Response id: `635f82e5-ef6b-48eb-acc6-65bec35ff7fb`
  - Question id: `25c1c87c-b13d-44ba-8548-9cfb763ac3de`
  - Question text: `In what year did the Western Roman Empire fall?`
  - `answered_correct=false`, `used_hint=true`
  - Correct answer in `question_bank`: `476 AD`

### Challenge Room
- Session table: `challenge_sessions`
- Session id: `789077c8-58ff-45ff-af4c-4e16a0a0a07a`
- User: `user1`
- Topic: `History`
- Start/end level: `1 -> 2`
- Totals: `total_questions=1`, `correct_answers=0`, `rank_points=-1`
- Answer persistence table: `challenge_answers`
  - Question id: `dd27e9a7-eae0-48a4-83b6-e4e4c1c82d42`
  - Question text: `Which ancient city was the capital of the Inca Empire?`
  - Chosen answer: `Machu Picchu`
  - Correct answer: `Cuzco`
  - `is_correct=false`, `points_change=-1`

### Custom Room
- Session table: `custom_sessions`
- Session id: `94377c0b-2477-4c6f-8656-21df3e431313`
- User: `user1`
- Topic: `History - World War II`
- Totals: `total_questions=1`, `correct_count=0`
- Response persistence table: `user_responses`
  - Response id: `2508fd5d-47a3-4f2b-a1b0-0f7b62700caf`
  - Question id: `e6087bf8-9763-4f4c-b55f-3b8c3bd1b385`
  - Question text: `How did Winston Churchill's 'We shall fight on the beaches' speech affect the British people's morale?`
  - `answered_correct=false`, `used_hint=false`
  - Correct answer in `question_bank`: `boosted their morale`

### PvP Room
- Match table: `pvp_matches`
- Match id: `adeccf54-0bce-474b-98dd-652bdcac2f40`
- Players:
  - `user1_id=a9c01983-c004-4732-b036-06f7cc03f776` (`user2`)
  - `user2_id=5e9eee47-a0b7-4ebc-897b-055396916018` (`user1`)
- Topic: `History`
- Match result: `completed`, `user1_score=1`, `user2_score=1`, `winner_id=null` (draw)

#### PvP Question Selection Logic (Observed)
- Selected questions are persisted in `pvp_matches.questions_json`.
- This run stored `5` shared questions in `questions_json` (indices `0..4`), including ids:
  - `e91e0aab-8f1e-4e0b-9e25-5030178b907e`
  - `b9933818-f057-46e8-bb05-7b0e60471fbb`
  - `10c659bc-239f-4e2b-9dbb-8ef5a41a5b99`
  - `8f909a53-c201-4037-aa13-78717256fe5f`
  - `b6a7dc6e-283f-491a-ac83-da7753661c21`

#### PvP Answer Persistence (Observed)
- Answer table: `pvp_match_answers`
- Recorded answers in this smoke run:
  - `user1_...` index `0`: question `e91e0...`, answer `Thebes`, `is_correct=true`
  - `user2_...` index `0`: question `e91e0...`, answer `Thebes`, `is_correct=true`
- This confirms both players received the same question id for the same index.

### Visual Room
- Session table: `visual_sessions`
- Session id: `6f225f6e-0907-448e-9fb1-b087168c3670`
- User: `user1`
- Topic/level: `Mixed`, level `1`
- Session state: `is_completed=true`, `current_index=1`, `score=0`
- Seen question ids persisted in `seen_ids_json`:
  - `665bc03a-7c43-4be4-a2b3-d623425e1000`
  - `efd3b184-9f39-4dfa-b121-c900764edaf8`
- Corresponding `visual_questions` rows:
  - `What event is depicted in the image?` (answer: `The Indonesian National Revolution`)
  - `Which country is represented by this flag?` (answer: `South Africa`)

## Data Persistence Map
- Classic interactions: `classic_sessions` + `user_responses` (+ `question_bank` lookup)
- Challenge interactions: `challenge_sessions` + `challenge_answers` (+ `question_bank` lookup)
- Custom interactions: `custom_sessions` + `user_responses` (+ `question_bank` lookup)
- Visual interactions: `visual_sessions` + `visual_questions`
- PvP interactions: `pvp_matches` + `pvp_match_answers` (+ inline `questions_json`)
