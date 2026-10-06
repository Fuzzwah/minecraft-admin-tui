# Spec Delta

## Purpose

Give visitors small, repeatable reasons to return to the hub through fair daily rewards and harmless activities that do not replace progression on destination servers.

## ADDED Requirements

### Requirement: Provide one daily present per player
The hub MUST provide a clearly marked daily-present chest or equivalent claim point. Each player MUST be able to claim at most one present during the configured hub day, and the hub MUST identify the player rather than relying on physical chest contents alone.

#### Scenario: Player claims the daily present
- **WHEN** a player with no claim for the current hub day uses the daily-present claim point
- **THEN** the hub grants one configured reward set
- **AND** records that player's claim for the current hub day

#### Scenario: Player claims twice
- **WHEN** a player uses the daily-present claim point again during the same hub day
- **THEN** the hub grants no additional reward
- **AND** explains when the next claim is available

### Requirement: Keep daily rewards bounded and harmless
Daily-present rewards MUST come from an administrator-configured allowlist of fun, cosmetic, decorative, or low-impact items. Rewards MUST NOT include operator permissions, executable commands, unrestricted container controls, or quantities intended to bypass destination-server progression.

#### Scenario: Reward configuration contains an unsafe entry
- **WHEN** the hub loads a reward entry that is malformed, executable, unauthorized, or outside configured quantity limits
- **THEN** the entry is rejected before it can be granted
- **AND** the remaining valid reward configuration remains available

### Requirement: Provide repeatable hub activities
The hub SHOULD provide activities such as trivia, hidden-item hunts, login streaks, and weekly challenges. Each activity MUST expose its eligibility, completion result, and reward or recognition outcome to the player.

#### Scenario: Player completes a repeatable activity
- **WHEN** a player satisfies the published conditions for an activity
- **THEN** the hub records completion for that activity and player
- **AND** grants only the activity's configured bounded outcome

### Requirement: Isolate activity state per player
Activity progress, daily-present claims, and streaks MUST be keyed to the authenticated Minecraft player identity. One player's completion MUST NOT satisfy another player's claim or alter another player's record.

#### Scenario: Two players use the same activity
- **WHEN** two players complete or attempt the same activity independently
- **THEN** each player's eligibility and result are evaluated separately
