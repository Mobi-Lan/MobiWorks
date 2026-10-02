# MabinogiMobile_CLI 레퍼런스

게임의 `MabinogiMobile_CLI.exe` 가 제공하는 것 전부. 카탈로그 원본은 게임이 주는 `capabilities` 응답이며,
`%LOCALAPPDATA%\MabinogiMobileCLI\CAPABILITIES.json` 에도 남는다. 각 명령의 정확한 필드·에러코드는 그 원문의
`OutputExample` / `Note` 를 볼 것 — 이 문서는 무엇을 만들 수 있는지 판단하기 위한 요약이다.

> CLI 를 실제로 실행하는 테스트는 실행 중인 게임을 조작하므로 **사람이 확인할 때만** 한다. 평소 검사는 가짜 CLI 로 돈다.
> 이 문서의 내용은 CLI 실행 없이 카탈로그와 MobiFolio 코드에서 읽은 것이다.

---

## 1. 호출 규약

```
MabinogiMobile_CLI.exe <command> [body]
```

- **body 가 비ASCII 면** UTF-8 → base64 후 `base64:` 접두를 붙인다 (JSON body 도 통째로).
  JSON 을 `ensure_ascii=True` 로 직렬화하면 base64 없이 보낼 수 있다.
- stdout 은 **바이트로 받아 utf-8 → mbcs 폴백** 으로 디코드한다.
- **`exit 0` 이 성공이 아니다.** `ok = (exit == 0) and ("error" not in body)`.

| exit | 뜻 |
|------|-----|
| 0 | ok |
| 2 | usage_error |
| 3 | canceled |
| 4 | unknown_command |
| 5 | disconnected |

- 응답은 stdout 과 `%LOCALAPPDATA%\MabinogiMobileCLI\last-response.json` 양쪽에 남는다.
  **`status` 와 `capabilities` 는 이 파일을 갱신하지 않는다** — 파일 폴백 로직을 쓰면 옛 응답을 읽게 되므로
  신선도 검사가 필요하다.
- `status` 는 카탈로그 밖의 로컬 명령: `{pipe: connected|disconnected, reason: game_off|…}`.
  **게임이 꺼져 있으면 약 5초 걸린다** → 15초 주기 백그라운드로만 부른다. 자주 부르면 UI 가 굳는다.
- `capabilities` 응답에 `loading: true` 면 캐릭터가 아직 입장 전이다. 다시 조회할 것.
- **파이프는 직렬이다. 한 번에 하나만.** 앱 쪽에서 잠금이 필수.
  실행 명령이 얼마나 오래 블로킹되는지는 **명세에 없다** (실측 최대 293초). 타임아웃 11분은 우리가 고른 안전 상한이다.
- **이벤트 푸시·콜백이 없다. 폴링만 가능하다.**

---

## 2. 읽기 명령 (조회만, 게임 상태를 바꾸지 않음)

| 명령 | 얻는 것 |
|------|---------|
| `capabilities` | 명령 카탈로그 |
| `get_activity` | 자동사냥 `{IsAutoPlaying, CanStartAutoPlay, 대상}`, 자동이동, 전투 `{IsDead, IsReviving, IsInCombat}`, 대화 `{진행중·다음가능·선택대기}`, 던전 `{State: NotInDungeon/Entering/InProgress/Cleared, 보스전}`, 전장, 어비스 결과, 튜토리얼, 시나리오, `Performance{IsPlaying, InstrumentName, MusicTitle, IsLoop, StartAt, Total/Elapsed/RemainingSeconds, ChannelCount}`, `Interaction{HasTarget, AvailableInteractionType(Gathering/Talk/None), TargetKind}`, `Mode{MainButtonState(Hide/Stop/Combat/Interaction/Compass/ScenarioQTE/Fishing/FishingPull/Housing), MountPartState, SitState, 미니게임, 하우징편집}` — 사실상 "지금 캐릭터가 뭘 하고 있나"의 전부. **실제 응답은 평면**(`IsDead`·`IsReviving`·`IsInCombat`·`IsWaitingForSelection`·`IsAutoPlaying` 이 최상위, `fixtures/get_activity.real.json`) — 카탈로그 예시의 `combatState`/`dialogue` 래퍼는 실측에 없다. `Dungeon`·`Battlefield`·`Scenario`·`Tutorial`·`Mode` 는 중첩 그대로 |
| `get_my_info` | 칭호·이름·레벨·직업, 전투력/생활력/매력/장식 점수, HP·공·방·STR/DEX/INT/LUCK/WILL·마법저항, 팔라딘 스탯, `Vitals{HP, 실드, 포만감, 가방 무게, 버프 수}`. 스탯은 `{DisplayName, Value}` 객체 |
| `get_current_environment` | 채널명, 지역명, 좌표, 날씨, **에린 시간(`ErinnNow`)**, `Housing{내 집 안인지·내 집인지·입장/퇴장 가능}` |
| `get_quests` | 퀘스트 트래커 `{제목, Source(main/pinned_sub/event/goddess_mission/…), Objectives[{설명, 완료, Count, Goal}]}`. **현재 탭에 보이는 것만** |
| `get_daily_missions` `get_weekly_missions` | `{Title, Description, CurrentCount, GoalCount, IsCompleted, IsRewardReceived, HasShortcut}` |
| `get_currencies` | `[{DisplayName, Amount}]` 주요 재화 |
| `get_inventory` | `{CurrentInventoryWeight, CurrentInventoryWeightAsDecimal, MaxInventoryWeight, MaxInventoryWeightAsDecimal}` |
| `get_items {category?, name?}` | **소모품 계열만** `{DisplayName, Category, CategoryDisplayName, Count, Location(inventory/account_storage/character_storage), IsLocked}`. Category 예: Food, Ingredient, Consumable, Consumable_Box, Consumable_Growth, Quest. **body 에 category 를 줄 때는 `CategoryDisplayName` 을 그대로** 쓴다. 장비·코스튬·펫은 안 나옴 |
| `get_craftable_items [필터]` | `{craftingUnlocked, items[{DisplayName, Craftable, ProducedPerCraft, Reason, MissingIngredients[{DisplayName, Required, Owned}]}]}`. `craftingUnlocked: false` 면 제작 미해금이고 items 는 빈 배열. `ProducedPerCraft` = **1회 제작 산출량**. Reason: `insufficient_living_skill_level`, `insufficient_facility_level`, `insufficient_decor_score`, `not_enough_ingredient`, `ingredient_locked`, `insufficient_transfer_cost` |
| `get_alterable_items [필터]` | `{items[{DisplayName, Alterable, ProducedPerWork, Reason, MissingIngredients[…]}]}` — **craftable 과 필드명이 다르다**(`Craftable`/`ProducedPerCraft` 가 아니라 `Alterable`/`ProducedPerWork`). `craftingUnlocked` 도 없다. Reason 에 `insufficient_living_skill_level` 없음 |
| `get_altering_works` | `{completedCount, works[{DisplayName, FacilityName, State, IsCompleted, RemainingSeconds}]}`. State: `NotStarted` / `InProgress` / `Completed`. 완료면 `RemainingSeconds` 는 0. **`FacilityName` 이 같은 작업들은 `complete_altering_work` 한 번으로 함께 수령된다** |
| `get_gatherable_items [필터]` | `{items[{DisplayName, ToolOk}]}` |
| `get_near_npcs` | 반경 30 의 대화 가능 NPC `{Name, Title, DisplayName, Distance}` |
| `get_near_pcs` | 반경 30 의 플레이어 — 이름·칭호·거리·레벨·직업, 전투력/생활력/매력, 친구/파티/길드 여부, 전투중, 옷 수·색 수·로브·후드·무기숨김, **그 사람의 `Performance`(연주 상태·곡명·남은 시간)** |
| `get_social_actions [필터]` | 행동 `{DisplayName, ChatCommands[]}` (예: `/전통댄스`), 표정 `{DisplayName, EmojiText}` |
| `get_music_scores [부분문자열]` | `{Location, DisplayTitle, IsCopyingAllowed, IsLocked}` |
| `get_instruments [부분문자열]` | `{Name, Durability, IsEquipped}` |

## 3. 행동 명령 (게임 상태를 바꿈 — 8종)

| 명령 | 하는 일 / 주의 |
|------|----------------|
| `write_chat <raw 50자>` | 채팅 전송. 행동(`ChatCommands` 원문)·표정(`EmojiText`)을 보내면 실행된다. `/지역`·`/파티` 등 예약 명령은 `unsupported_command`, 연타는 `rate_limited{retryAfterSeconds}`. **카탈로그에 `requiresConfirm=true`** — 사용자 확인 없이 보내지 말라는 뜻 |
| `play_music_score {title}` | 연주 시작. `not_found`/`no_instrument`/`not_available_on_combat`/`riding`/`dead`. 동명이면 게임이 한 장을 고른다 |
| `change_instrument {name}` | 악기 장착. `is_playing_instrument`/`level_requirement` 등. 이미 장착이면 `accepted` |
| `stop_action` | 연주·착석·자동사냥·운반·채집 등 "정지 버튼이 보이는" 행동 정지. 없으면 `invalid_state` |
| `stand_up` | `/앉기` 상태에서 일어남 (정지 버튼이 없어 `stop_action` 이 못 한다). 앉는 모션 중엔 timeout → 잠시 후 재시도 |
| `execute_gathering {displayName}` | 가장 가까운 채집지로 이동해 **최대 100개**까지 채집. 필요 소모품 보유량으로 상한이 줄고 `target` 으로 반환. 낚시 전용이면 자동낚시만 켜고 즉시 반환. 도중 UI 가 뜨면 `blocked{kind}`. **재화(정령의 날개) 소모** |
| `execute_crafting {displayName, craftCount}` | 시설로 이동 → 제작 → 결과 수령까지 블로킹. `craftCount` 는 **제작 횟수**(산출은 `ProducedPerCraft` 배). **재화 소모** |
| `execute_altering {displayName}` | 가공 1건을 큐에 등록(이동 포함, 비동기). N건이면 N번 호출. `requires_user_interaction` 레시피는 불가. **재화 소모** |
| `complete_altering_work {displayName}` | 그 아이템을 만드는 시설의 완료된 가공을 전부 수령(이동 포함). 시설당 한 번씩 |

> `execute_*` 는 재화를 태우고 수 분간 블로킹된다. **앱이 자동 반복하면 안 된다 — 사용자 확인 후 1회.**

---

## 3.5 재료 수집 실측 (2026-09-18, 새 캐릭터 레벨 23 / 본캐 레벨 100)

카탈로그 원문(`fixtures/capabilities.json`): *"Craftable reflects living skill level, facility level, decor score,
ingredient quantity, and **ingredient transfer cost**."* — `insufficient_transfer_cost` 는 **재료 이송 비용**이지 이동(정령의 날개) 비용이 아니다.

- **조회는 재화를 쓰지 않는다.** 7개 읽기 명령 전후 재화 16종 전부 동일 (실측).
- **계정 창고는 캐릭터 간 공유**다 (두 캐릭터의 `account_storage` 201종 완전 동일). **게임은 창고를 보유(`Owned`)로 세지 않는다** — 창고에만 있는 재료 3,478행 전부 `Owned=0`.
- 새 캐릭터(보유 0, 정령의 날개 0)로 조회 → 재료 확인 1,507, 미확인 345 (`insufficient_transfer_cost` 206 + `insufficient_living_skill_level` 139). **`Reason` 이 재료 부족이 아니면 `MissingIngredients` 가 빈다.**
- 같은 캐릭터에 정령의 날개 10개 넣고 재조회 → **변화 없음** (206 그대로). 정령의 날개는 무관하다.
- 막힌 206개는 본캐(골드 101만)에서 201개가 「가능」이었던 초보·중급 레시피들. 골드 30,000 + 정령의 날개 210 을 넣어도 **206개 그대로** — 골드·날개 소량으로는 안 풀린다.
- **판정 규칙 (실측으로 확정)** — 창고는 두 종류: 캐릭터 창고(`character_storage`, 캐릭터별)와 계정 공유 창고(`account_storage`, 전 캐릭터 공유).
  1. 어떤 재료라도 **가방 + 계정 창고**에 없으면 → `not_enough_ingredient` + 부족 목록 (`Owned` 는 **가방만** 센다)
  2. 전부 있는데 일부가 계정 창고에 있어 옮겨야 하고 그 이송 비용을 못 내면 → `insufficient_transfer_cost`, **목록 없음**
  3. 그 외 → 「가능」, 목록 없음
  증거: 가방에 「붕대 20」이 생기자 `내열 붕대`(붕대 5 + 튼튼 버섯 가루 3 + 불꽃의 결정 1) 등 6개가 `not_enough_ingredient` → `transfer_cost` 로 바뀌었다 — 붕대는 가방, 나머지는 계정 창고(80, 74)에 있었다. 앞서 "창고에도 없는 재료가 있는데 transfer_cost" 로 보였던 11개는 **동명 레시피의 다른 경로**를 잘못 대조한 것이었다(한 경로는 transfer_cost, 다른 경로는 재료 부족).
  이송 비용의 재화 종류는 아직 모른다 (골드 ≤30,000·정령의 날개 ≤210 으로는 안 됨).
- **따라서 재료를 보려면 그 레시피의 재료 중 하나라도 가방·계정 창고 양쪽에 없어야 한다.** 한 개만 없어도 규칙 1 로 떨어지면서 **모든 재료가 목록에 나온다** (`Owned` 가 가방 기준이라 가방이 비면 전부 부족). 계정 창고의 재료를 **캐릭터 창고로 옮기면** 다른 캐릭터에게는 안 보이므로, 그게 수집 방법이다.
- **본캐(레벨 100, 재료 충분)로 조회하면 재료가 거의 안 늘어난다** (1,576 → 1,580). 「가능」이면 부족분이 빈 배열이기 때문. **재료 수집엔 재료가 없는 캐릭터가 최적**이고, 비용을 채워 「가능」으로 만들면 오히려 재료가 숨는다.
- `Required` 는 캐릭터와 무관한 고정값 — 두 캐릭터 관찰이 1,925 레시피 전부 일치(conflicts 0).
- **채집 스킬·레시피 분류는 CLI 가 주지 않는다** → 앱은 이름 규칙으로 추정한다 (`categories.py`, 응답에 `inferred:true`,
  `data/categories.json` 으로 덮어쓰기). 실데이터 검증(2026-09-18): 채집 177종 중 미분류 1(「분해된 장비 부품」), 제작 1,835행 중 「기타」 2.3%.
- **앱의 보유 판정은 가방 + 계정 창고 합산**이다 (게임이 창고 재료를 원격으로 사용 — 이송 비용만 든다). `Owned` 숫자만 가방 기준.
  `work.plan()`/`apply_storage()` 의 `short` 는 합산 기준, `fromStorage` 가 창고에서 이송해야 할 양.

## 3.6 채집 도중 읽기·정지 실측 (2026-09-30, 대표 승인 하에 실기)

`execute_gathering` 이 도는 동안(블로킹 중) 다른 호출을 **잠금 없이** 겹쳐 보냈다.

- **읽기는 채집을 끊지 않는다.** `get_items`·`get_activity` 를 도중에 불러도 채집은 계속됐고, 가방 수가 도중에 올라갔다
  (한 회 안에서 6 → 16 → 64, 그 회는 100개로 끝남). → 예전 `docs/BOARD.md` §10.6·§10.7 의 「도중에 무엇을 보내든 채집이 취소된다」는
  **읽기에 대해서는 틀렸다.**
- **`stop_action` 은 `get_activity.Mode.MainButtonState` 에 따라 갈린다.**
  - `"Hide"`(채집지 사이를 이동 중): 거절 — `invalid_state` · 「No stoppable action is in progress right now.」
  - `"Stop"`: 수락 — 「Stop confirmed; no stoppable action remains.」 몇 초 뒤 막혀 있던 `execute_gathering` 이
    `ok=True · {result:"stopped", message:"Gathering stopped before reaching the goal...", gained: 8, target: 100, cost: "정령의 날개 5 spent, ..."}`
    로 돌아왔다. `stop_action` 자체는 날개를 쓰지 않는다(채집의 날개 5 는 시작 때 이미 나갔다).
- **`get_items {"name":"통나무"}`** 는 비슷한 이름(「부드러운 통나무」)까지 목록으로 준다 — `DisplayName` 이 정확히 같은 것만 센다.
- 연주 쪽: 가만히 있을 때 `play_music_score` → 「Play started」, 곧바로 `stop_action` → 「Stop confirmed」.
- **채집 중 실행 명령 (실측, 대표 승인)**: 통나무 채집 10초째 `execute_crafting {"displayName":"못","craftCount":1}` → 채집 호출이 **1초 안에**
  `error=canceled` (「Another command replaced this action…」, gained 0, 날개 5 는 이미 빠짐)로 끝나고 제작은 그대로 완료됐다.
  앱으로도 확인: 목표 도달 → `stop_action` 이 `invalid_state` → 다음 카드(못 제작)로 넘김 → 채집 `canceled` · 제작 `completed`.
- **아직 미검증**: 채집 중 `play_music_score` 로 채집을 밀어내는 것(다음 카드가 없고 `stop_action` 이 거절될 때의 길).

- **가공 칸 (실측, 2026-10-02 대표 승인)**: `execute_altering {"displayName":"가죽+"}` 을 연달아 — 1~7번 `result=started`(각 날개 5),
  8번째는 `error=blocked · kind=unknown_modal`(「A blocking UI is covering the screen…」)이고 **날개 5 가 빠졌다.** 게임에 막는 창이 남는다.
  `get_altering_works` 는 7건(1 InProgress · 6 NotStarted, 건당 RemainingSeconds 300) — 칸 수 필드는 없고 FacilityName 별 건수로 센다.
  게임 화면(대표 캡처): 가공 창 위에 「**가공 추가 실패** — 가공 대기열이 가득 찼습니다.」 팝업 · 시설은 「가죽 가공 시설 **Lv.6**」,
  아래 「모두 받기」 칸이 7개. 칸 수가 시설 레벨에 따라 달라질 수 있다(미확인) — 그래서 7을 박지 말고, 거절을 한 번 보면
  그때의 건수를 그 시설의 칸 수로 기억해 두는 편이 안전하다.
  → 등록 전에 그 시설의 건수를 세어 칸 수(기억값, 없으면 7)에 닿았으면 부르지 않는다.
  그 창은 **CLI 로 닫히지 않는다 (실측)**: 창이 뜬 채로 `play_music_score` → 「Play started」, `stop_action` → 「Stop confirmed」 이었지만
  창은 그대로였다. 이어서 `execute_gathering` → 즉시 `blocked · unknown_modal`(회신에 cost 없음 · 가방 그대로). `get_activity` 에는
  이 창을 알려 주는 칸이 없다(Interaction.LastRunningInteractionType="Altering", Mode.MainButtonState="Compass").
  → 창이 뜨면 사용자가 게임에서 닫을 때까지 실행 명령은 전부 막힌다. 앱은 멈추고 「창을 닫아 주세요」로 안내한다.

## 4. 실측에서 안 됐거나 믿기 어려운 것

- **이름 끝 공백**: `DisplayTitle`/`Name` 이 `"… "` 로 끝나면 `change_instrument`/`play_music_score` 가
  어떤 표기(원문·strip·base64)로도 `not_found`. **CLI 쪽 버그**. `strip()` 하면 정상 이름까지 깨지므로
  원문 그대로 보내고, 앱은 표시만 하고 사용자에게 게임에서 이름을 고치라고 안내한다.
- **동명 항목 지정 불가**: API 에 id 가 없어 어느 악보/악기인지 고를 수 없다. 앱 내부 채번으로만 구분.
- `get_activity.Performance.TotalDurationSeconds` 는 곡 시작 직후 0 이거나 비어 있다가 채워진다 →
  첫 폴링 몇 초는 무시. `IsLoop` 면 곡이 스스로 안 끝나므로 앱이 남은 시간을 보고 `stop_action` 해야 한다.
- `stop_action` 직후 상태 전이 중에는 `play_music_score` 가 `invalid_state` → **0.8초 간격 3~4회 재시도** 필요.
- `change_instrument` 는 캐시(`IsEquipped`)를 믿지 말고 항상 호출한다 ("Already equipped" 로 즉시 답한다).
- 실행 명령이 `blocked{kind}` 로 끝나면 **사용자가 게임에서 UI 를 닫아야** 한다. 앱이 자동으로 풀 수 없다.
- `write_chat` 자동 반복은 `rate_limited` 뿐 아니라 운영 정책상 위험하다.
- **탈것 탑승 중 (실측)**: `get_activity.Mode.MountPartState` 가 타면 `"Mounted"`,
  아니면 `"None"`. 탄 채로 `play_music_score` → `not_available_on_riding` ("Cannot play while riding."),
  `stop_action` → `invalid_state`, `stand_up` → `not_sitting`, `change_instrument` 는 **된다**,
  자동 이동은 탈것을 **그대로 둔다**, 행동·표정(`write_chat`)도 **안 내린다** → CLI 로는 못 내린다.
  폴리오는 탑승 중이면 재생을 보내지 않고 안내한 뒤 내리면 이어서 튼다.
- **제작은 탈것에서 내린다 (실측)**: 탄 채로 `execute_crafting {displayName, craftCount:1}`
  → `Mode.MountPartState` 가 `Mounted` → `"Dismounted"`(t+0.7~1.2초) → `"None"`(t+2.6~3.3초), 제작은 그대로 끝난다
  (8.7초 · 날개 −5 · 재료 소모). `execute_altering` 은 이동 중 탈것을 **안 내렸다**.
- **제작 곧바로 정지 = 재료 안 씀**: 정지 단추가 뜨자마자(t+0.9초, `Mode.MainButtonState == "Stop"`) `stop_action` →
  제작 회신 `ok=True · result "stopped_by_user"` ("Crafting was stopped before completion. Nothing new was produced."),
  날개 −5(접수 때 빠지고 안 돌아온다) · **재료는 그대로** · 캐릭터는 내린 채로 남는다.
  → 폴리오 설정 `folio_dismount_by_craft`(연주 탭 「탈것이면 날개 5 로 내리고 재생」, 기본 꺼짐)가 이 길로 내린다.

---

## 5. CLI 로 **못** 하는 것

방향을 잘못 잡지 않도록 명시한다.

- **이동·전투·스킬 사용·대화 선택·아이템 사용·장비 교체(악기 제외)·거래·우편·파티/길드 조작** — 전부 불가.
  행동 명령은 위 8종뿐이다.
- **자동사냥 켜기** — `CanStartAutoPlay` 는 보이지만 시작 명령이 없다. `stop_action` 으로 끄는 것만 가능.
- **장비·코스튬·펫·스킬 목록, 강화 수치** — `get_items` 는 소모품 계열만 준다.
- **채팅 수신** — 보내기만 있고 읽기가 없다. 파티·길드 채팅도 불가.
- 다른 플레이어의 상세(장비·스킬), **반경 30 밖의 무엇이든**.
- 시장·경매·상점 시세.
- 던전·전장 진행 조작 (상태 조회만).
- **여러 명령 동시 실행, 이벤트 푸시** — 없다. 폴링 + 직렬 파이프.
- **악보 파일 내용(MML) 읽기·쓰기·업로드** — 제목·위치·잠금 여부만.
- 게임이 꺼져 있거나 캐릭터 입장 전이면 전부 불가 (`game_off` / `loading`).

---

## 6. MobiWorks 가 쓰는 범위

생활 작업 관리자로 방향을 정했으므로 1단계는 **읽기 전용**이다.

| 쓰는 명령 | 용도 |
|-----------|------|
| `status` | 연결 상태 (15초 주기) |
| `get_craftable_items` `get_alterable_items` | 레시피와 부족 재료 |
| `get_altering_works` | 가공 대기열 타이머·완료 알림 |
| `get_items` `get_inventory` `get_currencies` | 재고·무게·재화 |
| `get_gatherable_items` | 채집 가능 목록 (도구 상태) |

`execute_crafting` / `execute_altering` / `complete_altering_work` / `execute_gathering` 은
**확인창을 붙인 뒤 2단계에서** 얹는다. 읽기 전용으로 시작하면 CLI 실행 승인 없이도 개발할 수 있다.

---

## 7. 큐 실행 규칙 (workqueue.py)

제작·채집 큐는 실행 명령(`execute_*`)을 부르는 유일한 경로다. 실행 명령은 **회당 정령의 날개 5개를 소모**한다 (카탈로그
`execute_gathering`·`execute_crafting`·`execute_altering` Note: "Running this command consumes 5 정령의 날개").

- **명시 시작만**: `POST /api/queue/start {confirm:true}`. 앱 재시작 후 자동 재개 없음 — `data/queue.json` 의 상태는 기동 시 `pending` 으로 되돌린다(`done` 은 유지).
- **체인 실행**: 항목이 끝나거나 오류가 나면 다음 항목으로 넘어간다 (`onError=continue`, 기본). `onError=stop` 이면 첫 오류에서 멈춘다.
  단 **다음 항목도 성공할 수 없는 오류**는 설정과 무관하게 체인을 멈추고 `stopReason=fatal:<error>` 로 남긴다 — `FATAL_ERRORS`:
  연결 계열(`cli_not_found`·`cli_disabled`·`spawn_failed`·`disconnected`·`game_off`·`timeout`)과 `blocked{kind}`(사람이 게임 창을 닫아야 함),
  게임 로딩이 끝나지 않음(`loading`).
- **실행 명령은 다시 보내지 않는다** (날개 5). **읽기는 짧게 다시 읽는다** (N4, 2026-09-30): 상태 점검 `get_activity`·가방 `get_items`·
  도구 `get_gatherable_items`·무게 `get_inventory` 가 `disconnected`·`timeout`·`game_off`·`cli_disconnected` 면 3초·6초 쉬고 두 번 더 읽는다
  (`READ_RETRY_WAITS`). 그래도 안 되면 그 코드 그대로 멈추고 안내는 「게임과 연결이 잠깐 끊겼습니다 — 재시도 2회 후 멈춤」.
  `loading`(재접속·캐릭터 선택 — 오류 `loading` · `blocked kind=loading` · 본문 `loading:true`)이면 5초마다 다시 보며 최대 3분
  기다렸다 잇는다 (`LOADING_POLL`·`LOADING_MAX`). ▶ 시작의 연결 확인(`status`)도 끊겨 있으면 같은 간격으로 두 번 더 본다(잠금 밖).
  큐가 도는 동안은 러너 스레드가 `SetThreadExecutionState(ES_CONTINUOUS|ES_SYSTEM_REQUIRED)` 로 윈도 절전을 막고 끝나면 푼다.
  오류 항목은 사용자가 `reset` 해야 다시 돈다.
- **채집 = 개수, 가방으로 세고 목표에서 끊는다** (N3, 2026-09-30 대표 결정 — 1.0.9 의 「n회」 입력은 반려됐다):
  사용자는 **개수**(`target`, 1~99,999 — 스테퍼는 100 눈금, 직접 입력 자유)를 넣는다. 회수는 ⌈target/100⌉ 로 따라 나오고
  화면은 「3회 · 날개 15 · 가방 47 → 목표 297」로 적는다. `ceil(target/100) > queue_max_passes`(기본 50)면 담는 순간 거부.
  진행·결과는 **가방 수**다: 시작 전 `get_items`(`progress.bag0`) → 매 회 뒤 `get_items`(`progress.have`),
  `progress.done` = 지금 가방 − `bag0` (카드 「2/3회 · +187/250개 · 가방 293」). 가방을 못 읽은 회만 회신 `gained` 로 메운다.
  **매 회 전에 `done >= target` 이면 끝낸다** — 목표에 닿은 뒤에는 다음 회를 부르지 않는다. `passesPlanned` 는 매 회 전
  「지나간 회 + ⌈남은 개수/100⌉」로 다시 센다(1회에 100개보다 적게 들면 늘어난다).
  **남은 개수가 100 보다 적은 회**(넘칠 수 있는 회)는 도는 동안 3초마다(`GOAL_POLL`) 잠금 없이 `get_items {"name": 이름}` 을 읽는다
  (§3.6 실측 — 읽기는 채집을 끊지 않는다. 비슷한 이름도 오므로 `DisplayName` 이 같은 것만 센다). 가방이 `bag0 + target` 에 닿으면:
  1. `stop_action` 1회. 받아들여지면 몇 초 뒤 채집 호출이 `ok · result=stopped · gained` 로 돌아온다 — **이 카드의 완료**다(실패·재시도 아님).
  2. `invalid_state`(이동 중 — `MainButtonState == "Hide"`)면:
     - **다음에 돌 작업 카드(채집·제작·가공·수령)가 있으면** 더 기다리지 않고 그 카드로 넘어간다. 그 카드의 실행 명령이 도는 채집을
       갈아치우고(카탈로그 「canceled = 다른 명령이 이 행동을 대신했다」) 채집 호출은 뒤에서 `canceled` 로 돌아온다 — **실측 확인**(§3.6).
       넘긴 동안 러너의 CLI 호출은 잠금 없이 나간다(넘긴 채집이 잠금을 쥐고 있다). 다음 카드의 상태 점검은 그대로 먼저 돈다.
     - **없으면** 지금 든 악기로 악보 하나(폴리오 대기열의 지금 곡, 없으면 보관함 첫 악보 — 탈것 위면 안 함)를 `play_music_score` 로
       틀어 채집을 밀어내고 곧바로 `stop_action` 으로 연주를 멈춘다 — **미검증**(§3.6).
     - 틀 수 없으면(악기·악보 없음·탈것·거절) `stop_action` 을 1.5초마다 최대 20번 다시 보낸다. `MainButtonState == "Hide"` 인 동안은
       보내지 않고 그 시도만 로그에 적는다. 끝내 안 되면 그 회가 끝날 때까지 둔다(넘친 만큼 더 캔다).
     넘기기·밀어내기·못 끊음은 카드 로그와 이벤트 줄(`kind: "gather"`)에 남는다.
  ■ 사용자 정지는 예전 그대로(`stop_action` 1회 → 카드 `stopped`). 목표 전에 게임이 `result=stopped` 로 끝낸 것은 예전처럼 이 카드의 오류다.
  두 회 연속 가방이 안 늘면 `no_progress`.
  호환: 요청의 `target`(개수)이 먼저, 1.0.9 화면이 보내는 `runs`(담기)·`count`(고치기)는 회수라 ×100 개로 읽는다.
  1.0.9 가 저장한 회수 카드(`runs`)는 불러올 때 한 번 `target = runs×100` 으로 옮긴다 — 1.0.9 가 1.0.7 의 개수 카드를 회수로 바꾸며 남긴
  로그 「개수 → 회수로 바뀜: 250개 → 3회」가 있으면 그 개수(250)로 되돌린다. 1.0.7 의 개수 카드는 그대로다.
  매 회 전에 `get_gatherable_items` 로 `ToolOk`(false → `tool_not_ok`), `get_inventory` 로 무게
  (`cur >= max - queue_weight_margin` → `overweight_soon`)를 확인한다.
  시작 뒤 끊긴 오류(`overweight`·`tool_broken`·`blocked`)도 응답의 `gained` 를 부분 획득으로 기록한다.
  **한 개도 못 캔 회차가 2회 연속**이면 `no_progress` 로 끊는다 (빈 병 등 소모품 소진 — 안 끊으면 상한까지 날개만 태운다).
  옛 저장본(`target` = 보유 + 부족분)은 로드 때 `progress.have` 를 빼서 한 번만 보정하고 `targetMigrated` 로 표시한다.
- **제작**: `execute_crafting {displayName, craftCount}`. 시설 상한(학습값 `recipe_limits.json`)을 넘는 횟수는 ⌈count/상한⌉ 번으로 나눠 부른다(N7, 호출마다 날개 5).
  상한을 모르면 한 번 통째로 보내 보고 `invalid_count(maxCount)`(시작 전 거절 · 날개 0)로 배워 그 자리에서 나눠 잇는다 — 카드를 실패로 두지 않는다. 자세한 것은 `docs/BOARD.md` §5.2.
- **가공 — 수령 모드 셋** (가공은 걸기만 하는 것이 기본이고, 수령까지 기다릴지는 고른다):
  항목 옵션 `collect` 가 `execute_altering` 으로 등록을 마친 뒤의 행동을 정한다. **모르는 값·생략은 `"none"`** 으로 담긴다(`add`).
  | `collect` | 등록 뒤 | 예상 호출(`passesPlanned`) |
  |---|---|---|
  | `"none"` (**기본** — 걸기만) | 다 걸면 그 자리에서 `done`. 수령하지 않는다 (수령은 `collect` 항목으로 따로 담는다). 칸이 차서 남았으면 `waiting` — 칸을 비울 만큼만 받는다 | `count` (화면의 예상은 7건을 넘는 몫의 칸 비우기 왕복까지) |
  | `"later"` | `waiting` → **곧바로 다음 항목으로**, 항목 전환 시·회차 끝·체인 끝에 `get_altering_works` 를 읽어 완료가 설정 n 개 모이면 `complete_altering_work` 로 수령하고 빈 칸에 남은 건을 다시 건다 | `count + 1` (화면의 예상은 7건마다 수령 1) |
  | `"wait"` | 같은 규칙으로 그 자리에서 끝까지 (남은 시간만큼 자며 5초~ · 진척 없이 30분이면 멈춤) | `count + 1` (〃) |
  **등록 직전마다 그 시설의 칸을 센다** (가공 칸 실측 아래 · N6) — 칸이 찼으면 부르지 않는다. 자세한 규칙은 `docs/BOARD.md` §5.2.
  다 건 `none` 인 항목은 `_collect_ready`·`_wait_pending_alters` 의 **대상이 아니다**. 그룹 `waitAlter=true` 는 **`later` 인 자식만** `wait` 로 올린다 —
  사용자가 명시한 「걸기만」을 그룹 설정이 뒤집지 않는다. **`collect` 필드가 없는 옛 저장본은 `"later"` 로 읽는다**(`alter_mode`) —
  `none` 으로 읽으면 이미 등록·결제된 가공을 영영 수령하지 못한다. 이관은 없다: 저장된 `"later"` 는 그대로 두고 새로 담는 것만 `none` 이 기본이다.
- **수령(독립 항목)**: 가공 대기열 카드의 시설별 「수령」이 `{type:"collect", facility, name}` 을 담는다 — `name` 은 그 시설에서 **완료된**
  작업 이름(CLI 가 이 이름으로 시설을 고른다; 진행 중 이름이면 `not_completed_yet`). 담을 때 캐시에 그 시설의 완료 작업이 없으면 `no_completed_work`,
  **같은 컨테이너(루트 하나 / 각 그룹 하나) 안에** 같은 시설 수령이 이미 대기·실행 중이면 `duplicate` — 컨테이너가 다르면 허용한다
  (그룹은 반복 단위라 루트의 수령과 그룹 안의 수령은 서로 다른 때에 도는 별개 작업이다). 실행은 `complete_altering_work` 1회(**수령은 날개를 쓰지 않는다** — `WING_COMMANDS` 밖, §10.5) → 성공 시 `collected`·`rewards` 를
  로그에 남기고 `get_altering_works` 를 다시 읽어 대기열 카드를 갱신한다. 가공 항목의 2단계 수령도 같은 함수(`_exec_collect`)를 쓴다.
  **완료 감지 직후의 `no_completed_work`·`no_completed_work_at_facility` 는 오류가 아니다** (실제 사고 2026-09-28 「면」: `get_altering_works`
  가 Completed 로 답한 같은 초에 `complete_altering_work` 가 `no_completed_work` — 조회와 수령 쪽 캐시가 완료 순간에 어긋난다).
  `_collect_ready` 는 `not_completed_yet` 처럼 카드를 `waiting` 으로 두고 `COLLECT_RETRY_WAIT`(5초) 뒤 대기열을 **다시 읽어**
  수령한다(없으면 「이미 수령」 done · Completed 면 수령 · InProgress 면 대기) — 항목당 `COLLECT_RETRY_MAX`(3)번, 그 뒤는 기존 오류.
  이 거절은 이동 전에 돌아오고 수령은 날개를 쓰지 않아 장부에 날개 0 으로 남는다. 「수령」 카드(`_run_collect`)는 이 재시도를 타지 않는다
  (누를 때의 `no_completed_work` 는 담은 뒤 상황이 바뀐 것 — 정당한 오류).
- **그룹과 반복**: 반복 단위는 **큐 전체가 아니라 그룹**이다.
  `config.repeat` 는 없어졌다(`{op:"config", repeat}` 는 `{"ok":false,"error":"gone"}`). 저장본에 `config.repeat > 1` 이 남아 있으면
  기동 때 루트 항목 전부를 그룹 「큐 반복」 하나로 감싸고 `config.repeat` 를 지워 저장한다 — 이관은 정확히 한 번.
  - 그룹 항목: `{type:"group", name(1~24자), repeat(1~20), loop, onError, retryFailed, waitAlter, status, log, items[]}`.
    **깊이 1단** — 그룹은 그룹을 품지 못한다. `id` 는 그룹 안팎을 통틀어 유일하다(러너가 id 커서를 쓴다).
  - 회차: `for loop in 1..repeat` — 회차 시작에 자식을 `pending` 으로 되돌리고 회차별 진행은 초기화, 누적 `totalDone` 은 유지.
    `retryFailed=false` 면 지난 회차에 `error` 로 끝난 자식은 건드리지 않고 건너뛴다. `waiting`(등록·결제 끝난 가공)은 어느 쪽이든 되돌리지 않는다.
  - 오류: 그룹 안 오류는 그 그룹의 `onError` 를 본다 — `stop` 이면 **그 그룹만** 멈추고(`status=error`) 보드는 다음 루트 항목으로 간다.
    `FATAL_ERRORS` 는 그룹 설정과 무관하게 **보드 전체**를 멈춘다. 그룹 밖 단일 카드는 그룹 설정을 물려받지 않고 보드 전역 `config.onError` 만 본다.
  - `waitAlter=true` 면 그룹 안 `alter` 자식을 등록하고 완료까지 기다렸다 다음 자식으로 간다 (항목 옵션 `collect:"wait"` 와 같은 경로).
    단 **`collect:"later"` 인 자식만** 올린다 — `collect:"none"`(걸기만)은 건드리지 않는다.
  - 편집 op: `group_create` · `group_update` · `group_dissolve` · `group_duplicate` · `move_item`(기존 `move` 를 대체).
    `add`·`chain` 에 선택 필드 `group`(담을 곳). 거절 코드는 `group_running`(실행 중 그룹의 구성 변경 — **회차 늘리기만 허용**) ·
    `nested` · `not_found` · `busy`(러너가 도는 중 루트 순서 변경) · `bad_request`.
  - 상태를 바꾼 응답에는 `GET /api/queue` 와 같은 모양의 `state` 를 실어 준다 (화면이 통째로 다시 그린다).
  - `GET /api/queue` 는 카드마다 `column`(`wait`/`run`/`done`/`fail`)을, 그룹에는 `summary` 를 **서버가 계산해** 실어 준다.
    UI 가 `status` 를 다시 해석하지 않는다. 30px 오버레이 밴드가 이 한 번의 조회만 보고 그리므로 **CLI 를 부르지 않는 가벼운 조회**를 지킨다:
    `columns`·`progress`·`current`·`currentCard.text`·`currentGroup`·`currentGroupRound`·`lastError{code,short}`·`stopReason`.
- **정지**: 플래그 + 실행 명령이 파이프를 잡고 있으면 `stop_action` 1회를 **잠금 없이** 보낸다. 카탈로그: "canceled means another command replaced this action" —
  다른 명령이 오면 진행 중 행동이 `canceled` 로 끝나므로, 정지 신호는 실행 명령이 쥔 잠금(최대 11분)을 기다리면 안 된다.
- **날개 소모 차단기**: 날개 5 는 실행 명령이 **수락되는 순간** 빠진다(실측 — 뒤에 `stopped_by_user`·`blocked` 로 끝나도 그대로).
  그래서 `_exec` 한 군데에서 `execute_gathering`·`execute_crafting`·`execute_altering` 의 답마다 `(시각, 날개, 헛소모, 명령, 이름)` 을
  메모리 기록(`Queue._wing_log`)에 남기고(`wing_spend`), 두 상한 중 하나에 걸리면 ■ 정지와 같은 플래그(`_stop`)로 체인을 세운다.
  | 상한 | 설정 (범위) | 창 | 걸리면 |
  |---|---|---|---|
  | 총량 | `wing_cap_total` 150 (50–1000) | `wing_cap_window_min` 10분 (1–120, 설정) | 합계가 상한을 **넘으면** (150 → 31번째 호출) `stopReason: "fatal:wing_cap"` 「날개 소모 이상 — 10분에 150 넘게 소모, 강제 정지」 |
  | 헛소모 | `wing_cap_waste` 4 (2–20) | `wing_cap_waste_min` 5분 (1–120, 설정) | 산출 없는 소모가 상한에 **닿으면** `stopReason: "fatal:wing_waste"` 「날개 헛소모 — 5분에 산출 없는 소모 4회, 강제 정지」 |
  - 날개를 쓴 것으로 치는 것: 성공 답 전부, 실패 답 중 `cost` 줄("정령의 날개 5 spent")이 있는 것, `cost` 가 없어도 아래 무료 거절이 아닌 실패 전부(모르면 센다).
    무료 거절(`WING_FREE_ERRORS`, `cost` 가 없을 때): `not_in_field`·`not_enough_ingredient`·`not_found`·`blocked kind=dead`(실측 0.1초·날개 0),
    `not_enough_currency`(낼 날개가 없다), `cli_not_found`·`cli_disabled`·`cli_disconnected`·`spawn_failed`(게임까지 가지 않았다).
  - 헛소모: 날개를 썼는데 산출이 없는 것 — 성공 답의 `result` 가 `stopped_by_user`·`stopped`·`canceled`, 또는 실패 답(`blocked` 모든 kind·`canceled`·`timeout`·그 밖의 오류).
    채집이 한 개라도 캤으면(`gained > 0`) 산출이 있으니 헛소모가 아니다. 우리 ■ 정지가 끊은 것도 헛소모로 세지 않는다(총량에는 센다).
  - 정지 모양: 배너 코드 `wing_cap`/`wing_waste`(좁은 폭 빨간 `kb-banner` · `viewmodel.chain_banner`), `lastError` 도 같은 코드(카드 오류가 뒤따라도 덮지 않는다),
    회신 기록 `stop` 이벤트·카드 로그, 오버레이 밴드 「날개 상한」/「날개 헛소모」. 다시 돌리려면 사람이 ▶ 시작을 누른다.
  - **▶ 시작은 창을 지우지 않는다.** 날개를 쓸 일(대기 채집·제작·가공 카드나 남은 회차)이 있는데 지금 합계 + 5 가 총량 상한을 넘거나
    헛소모가 이미 상한이면 `{"ok": false, "error": "wing_cap"|"wing_waste", "message": "<같은 문장> — 약 N분 뒤 다시 시작할 수 있습니다"}` 로 거절한다.
    기록은 메모리에만 있다 — 앱을 다시 켜면 비어 있다.
  - `GET /api/queue` 에 `wings10m`(최근 10분 날개 합)·`waste5m`(최근 5분 헛소모 횟수), `/api/queue/preview` 에 `wingGuard{wings10m, waste5m, cap, wasteCap, capMin, wasteMin}`.
- 러너가 도는 동안 `/api/sync` 는 `busy` 로 거부한다 (파이프 직렬).
- **선행 제작까지 담기** `{op:"chain"(옛 이름 "add_chain" 도 받는다), id, count, mode:"need"|"max", dryRun?, group?, collect?}`: 부족 재료(가방+창고 합산)를 `recipedb.ingredient_source` 로
  채집/제작/가공 항목으로 재귀 해결(깊이 4, 순환은 중단·경고, 같은 재료는 합산)해 **선행 → 목표** 순서로 담는다. 공급 경로가 없으면 `unresolved`.
  `collect`(가공 수령 모드)는 **목표 레시피 항목에만** 실린다 — 사용자가 고른 것이 그것뿐이다. **선행으로 딸려 들어가는 가공은 언제나 `"none"`(걸기만)**:
  그 산출을 다음 항목이 써야 하므로 수령을 강제하면 안 되고, 수령이 필요하면 「수령」 항목을 따로 담는다. 생략하면 목표도 `"none"`.
  응답의 `calls` 는 수령 호출까지 세고 `wingCalls`(= `wings / 5`)는 **날개를 쓰는 등록·채집·제작만** 센다.
- **최대 회수(날개 효율)**: `/api/plan` 의 `maxByStock`(= min ⌊(가방+창고)/1회 필요량⌋, 재료 정보가 없으면 null·`maxByStockPartial`), `maxCount`(시설 상한 —
  `execute_crafting` 이 `invalid_count` 로 거부할 때 응답의 `maxCount` 를 `data/recipe_limits.json` 에 학습), `maxSuggested = min`. `add`/`update` 의 `count:"max"` 는 그 값으로 치환.
- **실행 전 상태 점검** (2026-09-26 실측 기반으로 다시 씀 — 원 기록 `docs/reports/measure/README.md`): 항목마다 실행 명령을 부르기 **전에**
  `get_activity`(읽기, 비용 없음) 1회. 평면(실측)·중첩(카탈로그 예시 `combatState`/`dialogue`/`autoPlay` 래퍼) 모양을 둘 다 읽는다.
  **막는 것은 「보내면 날개만 타거나 어차피 못 가는데, 조회로 미리 알 수 있는 것」뿐이고**, CLI 가 공짜로 거절하는 상태는 CLI 의 답에 맡긴다.
  막는 순서: 대화 선택 대기 → 던전 → 지역 임무 → 연출. 막히면 실행 명령을 부르지 않고 그 항목을 오류로 끝내며 체인을 멈춘다(FATAL).
  `get_activity` 자체가 실패해도 그 오류로 멈춘다. 설정 `queue_precheck`(기본 true)로 끌 수 있다. `/api/queue/preview` 도 같은 점검을 실시간으로 해 `activity` 로 돌려준다.

  실측(품목 `철괴(철 광석)`, `execute_altering` 한 번씩 — 시간은 명령 왕복, 날개는 명령 전후 차이):

  | 상태 (조회로 읽는 곳) | CLI 답 | 시간 | 날개 | 우리 처리 |
  |---|---|---|---|---|
  | 일반 마을 | `result: started`. t+1.2초에 이동 퀘스트 「가까운 금속 가공 시설로 이동」(`Source: "shortcut"`, 목표 「철괴(철 광석) 가공」, `<color=orange>` 태그가 섞여 옴)과 `IsAutoPlaying=true` · `AutoPlayTarget="shortcut"` · `Mode.MainButtonState="Stop"` · `IsAutoTraveling=true` 가 함께 선다. 날개는 수락 즉시(t+1.2초) 빠진다 | 22.5초 | −5 | 정상. 답이 오면 로그 「출발·등록 확인: result=started …」 (호출이 막혀 있어 도중 폴링은 하지 않는다) |
  | 필드 전장 (`Battlefield.IsInBattleField=true`, 창백한 산), 비전투 | `started` — 스스로 전장을 나와 시설까지 이동해 등록 | 49.8초 | −5 | **경고만** 「전장 안(…) — 시설로 가는 이동이 안 될 수 있습니다. 실행해 보고 CLI 의 답으로 판단합니다」. 실패하면 문구 뒤에 「전장 안에서 실행 — …」 |
  | 전투 중 (`IsInCombat=true`) | `started` — 출발한다. 사람이 직접 멈추자 `result: stopped_by_user` | 약 7초 (7.7 · 6.2) | −5 | **경고만** 「전투 중(IsInCombat=true) — 출발은 되지만 몬스터에 붙잡히면 이동이 끊기고 날개는 소모됩니다」. 끊기면 문구 뒤에 「전투 중 출발 — 이동이 끊긴 것으로 보입니다」. `precheck_combat` 은 더 내지 않는다 |
  | 이동 중 끊김 (몬스터 어그로·직접 정지) | `ok=True`, `result: "stopped_by_user"`, 「The action stopped before it started, during travel or at the start…」 | 38초 | −5 | 그 카드의 오류 `stopped_by_user` 「게임에서 이동·작업이 끊겼습니다(전투·직접 정지) — 날개는 이미 소모됐습니다. ↻ 로 다시 시도하세요」 (가공·제작·채집 공통). 우리 ■ 정지 중이면 `stopped` |
  | 집 안 (`get_current_environment.Housing.IsInHousing=true`) · 던전 (`Dungeon.State="InProgress"`) · 인스턴스 전장 검은 구멍 (`IsInBattleField=true`) | `error: not_in_field` 「Auto-travel cannot be used in this place. The user must leave this place before continuing.」 | 0.1초 | 0 | `not_in_field` 는 FATAL(체인 정지) 「자동 이동을 쓸 수 없는 곳(집 안 등)입니다 … 나온 뒤 ↻」. 던전은 조회로 보이므로 `precheck_dungeon` 사전 정지를 그대로 둔다 (어느 쪽이든 비용 없음) |
  | 사망 (`IsDead=true`) | `error: blocked`, `kind: dead` 「The character is dead or reviving… The user must pick a revive option first.」 | 0.1초 | 0 | **우리가 막지 않는다** (`precheck_dead` 제거 — 경고 「사망/부활로 보고됩니다 …」만). CLI 의 사망 깃발은 부활 직후 **잔상**이 남는다(조회는 `IsDead=false` 인데 CLI 는 `kind=dead`) → 로그 「게임이 사망으로 답했습니다 — 3초 뒤 한 번 다시 보냅니다 (부활 직후 잔상)」 후 **같은 명령을 한 번만** 다시 보낸다(정지 중이면 안 보냄). 또 `kind=dead` 면 `blocked`(FATAL) 「사망/부활 상태로 보고됩니다 — 부활을 고른 뒤 ↻」. `_exec` 한 군데라 가공·제작·수령·채집 공통, 이 거절은 장부에 날개 0 으로 적는다 |
  | 지역 임무 진행·정산 중 (`IsAutoPlaying=true` 이면서 `AutoPlayTarget=="goddess_mission"`) — 창백한 산 사냥 IV 진행 중, 또는 완료 뒤 전리품 대기 | 출발해 여신상까지 워프(추가 워프 비용)한 뒤 「…입구로 이동하시겠습니까? N개의 완료하지 못한 지역 임무는 포기하게 됩니다」 확인창 또는 「사냥터 클리어!」 결과 화면에서 멈춤 → `error: blocked`, `kind: unknown_modal` 「A blocking UI is covering the screen…」. 이 창은 `get_activity` 에 안 보인다 (대화 깃발 전부 false) | 14–18초 | −5 (+ 워프 −8) | **보내기 전에 정지** `precheck_mission`(FATAL) 「지역 임무 진행 중(AutoPlayTarget=goddess_mission) — 나가면 임무 포기 확인창·결과 화면에 막힙니다 (날개만 소모). 임무를 끝내고 전리품까지 받거나 포기한 뒤 ↻」. 오버레이 짧은 글 「지역 임무 중」. 가공·제작·수령·채집 모두 |
  | 그 밖의 `blocked kind=unknown_modal` | 위와 같음 | — | — | `blocked`(FATAL) 「게임에 확인창·결과 화면이 떠 있습니다 — 닫은 뒤 ↻」 (원문은 「 · 원문: …」 으로 뒤에 남김). 그 밖의 kind 는 CLI 원문 그대로 |

  - 그대로 두는 사전 정지: 대화 선택 대기(`IsWaitingForSelection`) → `precheck_dialog`, 던전(`Dungeon.State != NotInDungeon`) → `precheck_dungeon`,
    시나리오·튜토리얼·어비스 연출 → `precheck_scenario`. 자동사냥(`IsAutoPlaying`, 대상이 `goddess_mission` 이 아닐 때)은 경고만.
  - 더 내지 않는 코드: `precheck_dead` · `precheck_combat` · `precheck_battlefield` — 옛 기록(저장된 카드·로그) 때문에 `PRECHECK_ERRORS`·`ERROR_KO`·`ERROR_SHORT` 에 남긴다.
  - 점검 통과 뒤에도 `blocked` 가 오면 로그에 「게임 상태 보고 불일치 가능」을 남긴다 (실제 사고 2026-09-18: 멀쩡히 서 있는데 `complete_altering_work` 가 `blocked kind=dead` — 위 잔상 재시도가 그 답이다).
  - 이동이 실행 명령 안에 들어 있으므로 실행 명령 타임아웃(`EXEC_TIMEOUT` 660초)은 가장 긴 이동(49.8초)보다 넉넉해야 한다 — 낮추지 않는다.
  - 실측하지 않은 것: `execute_crafting`·`complete_altering_work`·`execute_gathering` 을 위 상태들에서 부른 것 (같은 이동 경로를 탄다고 보고 같은 규칙을 쓴다).
- **연주 중 규칙** (설정 `queue_on_performance`, 레일 「우선」 3단 — 위 ♪「연주」 · 가운데 ⏭「이 곡」 · 아래 ∞「작업」, 누를 때마다 music → song → work → music):
  **2026-09-26 실측: 연주 중 execute_altering 을 보내면 거절 없이 날개 5 를 빼고 연주를 끊은 뒤 이동한다 — 보호는 큐가 보내기 전에만 가능.**
  (`Performance.IsPlaying=true`·반복 재생 중, 날개 −5 는 t+1.2초, 연주는 즉시 끊기고 이동 시작 — 사람이 직접 멈춤.)
  - `music` 「완전한 연주 우선」(기본): 연주 중이면 **연주 대기**(hold) — 왜 시작하지 않는지 보이도록 작업 중 칸에 대기 카드를 띄운다.
    `/api/queue/start` 가 `get_activity` 한 번(읽기)으로 연주를 보면 거절하지 않고 `{"ok": true, "hold": true}` 로 러너를 띄우고, 러너는 첫 카드 앞에서
    **아무것도 보내지 않은 채** 기다린다. 기다리는 동안 `GET /api/queue.hold` 가 카드다 (`kind` · `title` · `elapsed` · `left` · `text`):
    보드는 작업 중 칸 **맨 위**의 임시 카드, 실행 줄은 「연주 대기 · 제목」, 오버레이 밴드는 「연주 끝나면 시작」. 큐 항목이 아니라 저장하지 않고, 날개 0, 끝나면 사라진다.
    어디까지 기다리는가는 연주의 모양이 정한다 (폴리오 `engine.work_now`):
    우리 전체 재생 → 목록이 **다** 끝날 때까지 「연주 중 — 전체 재생이 끝나면 시작 (n곡 남음)」 (곡 사이 틈에는 시작하지 않는다 — `HOLD_SETTLE` 4초 조용해야 끝으로 친다) ·
    우리 한 곡 → 「연주 중 — 이 곡이 끝나면 시작」 · 게임에서 직접 튼 것 → 「게임에서 연주 중 — 끝나면 시작」. 반복 재생이면 「끝을 알 수 없음」이라 적고 그래도 기다린다.
    끝나면 **스스로 시작한다** (상태 점검을 다시 한다). ■ 정지는 대기를 거두고 카드를 지운다 (사유 `user`).
    무엇을 얼마나 읽나: 폴리오가 붙어 있으면 그쪽 감시가 재 둔 값을 **메모리에서** 2초마다(`HOLD_POLL`, CLI 호출 0) · 없으면 `get_activity` 를 5초마다(`HOLD_POLL_CLI`, 읽기·날개 0).
    도는 중에 연주가 시작돼도 같다 — 다음 실행 명령을 **보내지 않고 연주도 멈추지 않고** 그 카드 앞에서 연주 대기하고, 끝나면 그 카드부터 스스로 이어 간다.
    2026-09-26 의 `performance_playing` 거절과 `stopReason: "performance"` 정지는 더 내지 않는다 (옛 기록용으로 이름·문구만 남긴다). 확인창의 `performance_playing` 문구는 「기다렸다가 시작합니다」다.
  - `song` 「이 곡 끝나면」: 연주 쪽에 양보를 부탁하고 곡 경계에서 넘겨받는다 (`_yield_after_song`, 모비폴리오 엔진 핸드셰이크 — 바꾸지 않음).
    **보드가 끝나도 이어서 틀지 않는다** — 작업에 양보한 연주를 멋대로 다시 틀지 않는다. 끝나면 `perf_release`(`engine.work_release`)로
    양보 부탁만 거두고(`yield_at`·`held` 지움) 폴리오 대기열은 그 자리에서 **멈춘 채**(■ 「작업에 양보해 멈춤 — ▶ 로 이어서 재생」) — 다음 곡은 사람이 ▶ 를 눌러야 간다.
    `perf_resume`(`work_resume`, 이어서 틀기)을 부르는 곳은 `server._perf_yield` 가 **경계에 서기 전에** 부탁을 거두는 두 길(큐 정지·시한)뿐이다 — 그때는 음악이 끊긴 적이 없다.
    **부탁하고 기다리는 동안은 같은 연주 대기 카드**(`hold.kind: "song"`, 「연주 중 — 이 곡이 끝나면 시작」 · 밴드 「이 곡 끝나면 시작」)
    「노래 연주 중이면 큐에 연주도 잡으라고 하지 않았음?」(57초 동안 「실행 중 · 00:07」로만 보였다). 폴리오가 전체 재생이라 답해도 넘김은 다음 곡 경계라 「이 곡」.
    첫 카드 앞이든 도는 중이든 같다. 양보받으면 카드가 사라지고 그 카드의 경과가 **그때부터** 센다 (`start` 회신 한 번 더, `_hold_end`).
    기다리는 동안 도는 카드의 시계는 **선다**(00:00, `board.js cardStart` — hold.card 가 그 카드) · 머리·배지·실행 줄·대기 카드는 연주의 경과(`hold.started`) ·
    `_hold_set` 이 `hold` 회신도 남겨 실행 줄의 시계(`runStart`)가 그 뒤의 `start` 부터 센다 (2026-09-27 실측 「실행 중 00:58 / 카드 00:10」).
  - `work` 「작업 우선」: 지금 바로 `stop_action` 으로 연주를 끊고 진행한다. 합주·인사말 중(연주 쪽이 `may_stop=false`)이면 그것만 기다린다 (`PERF_WAIT_MAX`).
    **그 기다림도 같은 카드**(`hold.kind: "work"`, 「연주 중 — 합주/인사말이 끝나면 시작」 · 밴드 「합주·인사말 끝나면 시작」). 바로 끊는 경우는 기다림이 없으니 카드도 없다.
  - 상태 점검을 끄면(`queue_precheck=false`) 연주도 보지 않는다 (같은 조회에 실려 오므로 같이 꺼진다).
- **에러 이름의 출처**: `workqueue.py` 의 `GATHER_ERRORS`·`CRAFT_ERRORS`·`ALTER_ERRORS`·`COLLECT_ERRORS` 는 `fixtures/capabilities.json` 의 OutputExample 에서
  옮겨 적은 것이고 테스트가 카탈로그에 실재하는지 검사한다. 앱이 스스로 내는 것은 `APP_ERRORS`(`tool_not_ok`·`overweight_soon`·`max_passes`·`not_gatherable`·`not_in_cache`·`cli_disconnected`).
- 회신 기록: 채집·제작은 블로킹 응답이 곧 완료 회신 — `log` 에 "완료 회신: result=…, 획득 n개". 가공은 `collect:"later"`/`"wait"` 이면 "등록됨 → 완료 감지(폴링) → 수령 완료" 세 줄, `"none"`(걸기만)이면 "등록됨 → 등록 완료(수령하지 않습니다)" 두 줄.
  `/api/queue.events` 에 최근 100건의 타임라인(`loop`·`start`·`done`·`error`·`collect`·`stop`).
