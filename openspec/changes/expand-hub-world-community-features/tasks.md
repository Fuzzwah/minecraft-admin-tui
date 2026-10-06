# Tasks

## 1. Establish the managed feature layout and configuration contract

- [ ] 1.1 Define a bounded commons layout outside the existing x=0..255, z=16..143 destination-bay grid, including connector, community, reward, game, utility, exploration, collection, and event regions.
- [ ] 1.2 Add validated feature enablement and safe display/reward configuration handling without exposing RCON credentials or accepting executable player-authored data.
- [ ] 1.3 Extend generated forceload and world-boundary commands for the commons while preserving all stable bay, portal, control-sign, and owner-label coordinates.
- [ ] 1.4 Add generator validation that rejects region overlap, invalid coordinates, unsafe reward entries, malformed display text, and modifications outside the declared managed regions.

## 2. Implement community messages and moderation surfaces

- [x] 2.1 Generate the protected sign supply, dedicated sign wall, suggestion drop-off, welcome board, notice board, and curated showcase areas.
- [x] 2.2 Implement the interaction rules that allow supplied signs only on the message wall while retaining Adventure-mode protection elsewhere.
- [x] 2.3 Ensure visitor notes, book submissions, announcements, lore, and showcase text are emitted or stored as inert plain text with no player-controlled click events or commands.
- [x] 2.4 Add administrator refresh/cleanup behavior that separates suggestions from public notices and preserves player-owned message signs during rebuilds where required.

## 3. Implement daily presents and repeatable activities

- [ ] 3.1 Add persistent, namespaced scoreboard objectives for the hub-day counter, daily-present claims, activity cooldowns, streaks, and per-player completion state.
- [ ] 3.2 Generate the daily-present chest/claim area and trigger-based claim flow, including duplicate-claim messaging and atomic request consumption.
- [ ] 3.3 Validate configured reward allowlists and compile bounded, non-executable item grants with safe handling for invalid entries.
- [ ] 3.4 Implement at least one repeatable activity plus the published eligibility, completion, recognition, cooldown, and cleanup behavior for additional activity slots.

## 4. Implement games and records

- [ ] 4.1 Generate the first protected recreational course and its rules, start, finish, failure, reset, and cleanup regions.
- [ ] 4.2 Add per-player game session state, completion validation, personal-best tracking, and optional leaderboard display using bounded scoreboard objectives.
- [ ] 4.3 Add the remaining selected game templates or reserved disabled areas without allowing game controls to invoke destination lifecycle or transfer actions.
- [ ] 4.4 Verify abandoned sessions, entities, hazards, and temporary blocks are reset without manual repair.

## 5. Implement exploration, lore, and seasonal areas

- [ ] 5.1 Generate protected viewpoints, themed districts, secret routes, hidden interactions, and lore displays within the declared commons regions.
- [ ] 5.2 Add per-player discovery recognition with bounded cosmetic outcomes and no privileged commands.
- [ ] 5.3 Add replaceable seasonal decoration and centerpiece regions with explicit cleanup that preserves bays, messages, labels, and controller signs.

## 6. Implement utility and shared-resource areas

- [ ] 6.1 Generate labeled public crafting, processing, map, ender-chest, seating, and portal-orientation stations according to feature configuration.
- [ ] 6.2 Implement optional starter supplies or shared-resource areas with explicit item and quantity limits, clear labels, and no administrative/progression bypass.
- [ ] 6.3 Add cleanup and protection for abandoned inventories, dropped entities, fire, mobs, and other disruptions in utility regions.

## 7. Implement collectibles and event infrastructure

- [ ] 7.1 Generate protected displays for approved heads, banners, maps, item exhibits, seasonal collectibles, and contributor recognition.
- [ ] 7.2 Add per-player collectible discovery state and bounded cosmetic recognition without allowing exhibit removal or replacement.
- [ ] 7.3 Generate reusable stage, arena, build-competition, treasure-hunt, fireworks, and community-decoration regions with published boundaries and rules.
- [ ] 7.4 Add bounded event effects, durations, rewards, entity cleanup, and end-of-event restoration.

## 8. Integrate deployment and safe partial availability

- [ ] 8.1 Update explicit deployment/rebuild generation and datapack installation to install enabled features without changing routine controller polling or restart behavior.
- [ ] 8.2 Preserve existing state, owner-label signs, stable slot mappings, and destination controls during rebuilds; report disabled or invalid optional features clearly.
- [ ] 8.3 Ensure a disabled or failed optional feature does not prevent portal transfers, status display, or authorized destination start/stop controls.
- [ ] 8.4 Document configuration, rebuild requirements, moderation expectations, reward boundaries, and rollback/backup considerations in `README.md`.

## 9. Add regression coverage and verify the deployed behavior

- [ ] 9.1 Extend `tests/test_hub_world.py` to prove region boundaries, fill limits, stable-bay isolation, owner-label preservation, inert text handling, and datapack objective/trigger generation.
- [ ] 9.2 Add tests for daily claim idempotency, reward validation, per-player state isolation, game completion/reset, feature disablement, and event cleanup.
- [ ] 9.3 Add deployment/controller tests proving normal polling does not regenerate geometry and existing lifecycle authorization remains unchanged.
- [ ] 9.4 Run the focused hub-world tests, the full test suite, and a generated-command/datapack smoke scenario covering a fresh deployment and an explicit rebuild.
