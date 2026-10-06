# Spec Delta

## Purpose

Provide useful public services in the hub while preserving protection, keeping destination worlds authoritative for progression, and avoiding accidental resource or permission escalation.

## ADDED Requirements

### Requirement: Provide public utility stations
The hub SHOULD provide clearly labeled crafting, smelting, stonecutting, smithing, anvil, grindstone, map, ender-chest, seating, and portal-orientation areas where the corresponding service is intentionally enabled. Utility stations MUST remain accessible without allowing players to break or relocate them.

#### Scenario: Visitor uses a public station
- **WHEN** a visitor interacts with an enabled utility station
- **THEN** the station provides its normal Minecraft interface or information
- **AND** the visitor cannot modify the surrounding managed structure

### Requirement: Keep progression boundaries explicit
Hub utility areas MUST NOT silently grant destination-world progression, bypass destination permissions, or expose controller credentials. Any starter supplies, community farms, or free materials MUST be separately enabled, bounded, and labeled as hub-provided.

#### Scenario: Visitor uses a free-supply area
- **WHEN** a visitor takes an item from an enabled shared-resource area
- **THEN** only the configured item and quantity can be taken
- **AND** the area cannot be used to obtain administrative items or unlimited progression resources

### Requirement: Protect shared utility state
Shared utility areas MUST recover from abandoned inventories, dropped entities, fire, mob activity, and other ordinary gameplay disruptions without requiring visitors to repair the hub.

#### Scenario: Utility area is disrupted
- **WHEN** a visitor leaves items, creates a hazard, or abandons an interaction in a utility area
- **THEN** the hub's protection and cleanup behavior restores the area to its published safe state
