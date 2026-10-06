# Spec Delta

## Purpose

Support harmless collection, display, and community-event experiences that give the hub long-term identity without allowing visitors to rewrite managed structures or execute privileged actions.

## ADDED Requirements

### Requirement: Provide protected collectible displays
The hub SHOULD provide displays for mob heads, player heads, banners, maps, item exhibits, dyed items, seasonal collectibles, and other approved cosmetics. Displays MUST be administrator-managed or use a validated contribution path, and visitors MUST NOT be able to remove or replace protected exhibits.

#### Scenario: Visitor views a collection
- **WHEN** a visitor enters a collection area
- **THEN** the configured exhibits and labels are visible
- **AND** the visitor cannot take protected exhibits or alter their display data

### Requirement: Support hub-only collectible recognition
The hub MAY record harmless collectible discoveries or display a player's approved contribution. Recognition MUST be tied to the player's identity and MUST NOT grant operator permissions, destination controls, or unbounded items.

#### Scenario: Player finds a hidden collectible
- **WHEN** a player reaches the collectible's validated discovery condition
- **THEN** the hub records the discovery once for that player
- **AND** displays the configured cosmetic recognition or bounded reward

### Requirement: Provide reusable event spaces
The hub SHOULD provide a stage, seating, announcement area, tournament or arena space, build-competition plots, treasure-hunt routes, fireworks viewing area, and community-decoration area as appropriate. Event spaces MUST have explicit boundaries and published rules.

#### Scenario: Administrator runs a community event
- **WHEN** an administrator enables an event space and publishes its rules
- **THEN** visitors can participate within the assigned area
- **AND** visitors cannot change unrelated hub geometry or invoke destination lifecycle controls

### Requirement: Constrain event effects
Fireworks, arena effects, temporary decorations, and event rewards MUST be bounded to configured regions, durations, and allowlists. Event cleanup MUST remove temporary entities and state after the event ends.

#### Scenario: Event ends
- **WHEN** an administrator disables or expires an event
- **THEN** temporary event state is cleaned up
- **AND** permanent hub structures, messages, owner labels, and destination bays remain intact
