# Spec Delta

## ADDED Requirements

### Requirement: Preserve existing destination controls during hub expansion
Hub feature generation and updates MUST preserve stable destination bay identities, portal selectors, managed status and lifecycle signs, owner-label signs, and the controller's administrator and enrollment authorization behavior.

#### Scenario: Hub features are generated around existing bays
- **WHEN** the hub is generated or explicitly rebuilt with community features enabled
- **THEN** existing destination bays retain their stable locations and controls
- **AND** existing owner-label text is preserved

### Requirement: Keep managed regions protected
The expanded hub MUST keep visitors in Adventure mode or an equivalent protected state outside explicitly enabled interaction surfaces. Feature areas MUST declare their permitted interaction boundaries, and ordinary visitors MUST NOT break, place, or modify blocks outside those boundaries.

#### Scenario: Visitor leaves an interaction area
- **WHEN** a visitor attempts to break, place, edit, or activate a protected hub element outside an enabled feature boundary
- **THEN** the action is refused or has no effect
- **AND** the hub remains unchanged

### Requirement: Require explicit deployment for geometry changes
Adding or changing managed feature geometry MUST occur only through an explicit deployment or rebuild operation. A routine controller restart or status poll MUST NOT silently rewrite the hub world.

#### Scenario: Controller restarts after a feature configuration change
- **WHEN** the controller restarts without an explicit rebuild or deployment request
- **THEN** it does not regenerate managed feature geometry
- **AND** existing player-owned and managed content remains in place

### Requirement: Support safe partial feature availability
Feature configuration MUST allow an unavailable or disabled feature to remain absent without preventing the hub from serving destination bays, portal transfers, status display, and lifecycle controls.

#### Scenario: One optional feature cannot be installed
- **WHEN** an optional feature is disabled, unavailable, or fails validation during deployment
- **THEN** that feature is marked unavailable or omitted
- **AND** unrelated hub features and destination controls remain usable
