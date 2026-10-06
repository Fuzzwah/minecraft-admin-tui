# Spec Delta

## Purpose

Turn the hub into a small destination of its own through discoverable lore, secrets, viewpoints, themed spaces, and event-aware decoration that remains safe to explore.

## ADDED Requirements

### Requirement: Provide discoverable points of interest
The hub SHOULD include hidden buttons, secret rooms, viewpoints, themed biome districts, ruins, or other discoverable points of interest. Each point of interest MUST remain within protected hub geometry and MUST be reachable without breaking blocks or using operator commands.

#### Scenario: Visitor discovers a hidden point of interest
- **WHEN** a visitor follows the intended exploration path or activates the intended harmless interaction
- **THEN** the hub reveals the point of interest or its reward
- **AND** the visitor does not gain permission to alter unrelated hub blocks

### Requirement: Provide a lore trail
The hub SHOULD provide a sequence of readable signs, books, or displays that explain the hub's history, destinations, or community story. Lore content MUST be administrator-curated and inert.

#### Scenario: Visitor reads lore
- **WHEN** a visitor interacts with a lore display
- **THEN** the display presents the configured text
- **AND** it performs no command or controller action unless the action is an explicitly documented harmless interaction

### Requirement: Support seasonal presentation
The hub MAY provide seasonal decorations and a replaceable centerpiece or themed area. Seasonal changes MUST be bounded to their assigned display regions and MUST NOT remove destination bays, player messages, owner labels, or controller-managed signs.

#### Scenario: Seasonal theme is enabled
- **WHEN** an administrator enables a seasonal presentation
- **THEN** the assigned decorations and displays update
- **AND** unrelated managed and player-owned areas are preserved
