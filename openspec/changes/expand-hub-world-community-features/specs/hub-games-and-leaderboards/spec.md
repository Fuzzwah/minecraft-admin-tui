# Spec Delta

## Purpose

Add optional, protected recreational spaces that make the hub fun to visit while keeping game state isolated from destination worlds and preventing game areas from becoming grief vectors.

## ADDED Requirements

### Requirement: Provide protected recreational courses
The hub SHOULD provide one or more clearly marked courses or games, such as parkour, target practice, elytra or boat races, mazes, dropper sections, fishing contests, spleef, or hide-and-seek areas. Managed game geometry MUST remain protected from ordinary block breaking and placement.

#### Scenario: Player enters a game
- **WHEN** a player enters a published game start area
- **THEN** the hub presents the rules and start state
- **AND** the player cannot alter the course geometry

### Requirement: Detect completion and reset game sessions
Each enabled game MUST define a start condition, completion or failure condition, and reset behavior. A failed, abandoned, or completed session MUST NOT leave persistent entities, hazards, or altered blocks for the next player.

#### Scenario: Player completes parkour
- **WHEN** the player reaches the published finish condition
- **THEN** the hub records the completion and presents the configured result
- **AND** the course is ready for another session without manual block repair

### Requirement: Track optional personal records
Games MAY track each player's best score or time and MAY display a leaderboard. Records MUST be keyed to player identity, use a published scoring rule, and reject impossible or malformed results.

#### Scenario: Player beats a personal record
- **WHEN** a validated completion is better than the player's stored record
- **THEN** the new record replaces the old record
- **AND** the displayed score follows the published ordering rule

### Requirement: Keep games separate from destination control
Game interactions MUST NOT start, stop, transfer, reconfigure, or grant administrative access to destination servers. Game rewards, if enabled, MUST follow the bounded reward rules.

#### Scenario: Visitor interacts with a game control
- **WHEN** a non-administrator activates a game control
- **THEN** only the game action occurs
- **AND** no destination lifecycle or controller authorization state changes
