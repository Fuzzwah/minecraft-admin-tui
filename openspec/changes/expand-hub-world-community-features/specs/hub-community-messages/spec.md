# Spec Delta

## Purpose

Provide a safe, visible way for players to communicate, leave feedback, and contribute curated content in the hub without granting permission to modify managed geometry or execute commands.

## ADDED Requirements

### Requirement: Provide protected community message surfaces
The hub MUST provide a clearly marked community area containing a supply of ordinary signs and a designated surface where visitors can leave short notes. Sign placement or editing MUST be restricted to that surface; players MUST remain unable to break or modify unrelated hub blocks.

#### Scenario: Visitor leaves a note
- **WHEN** a visitor uses a supplied sign at the designated message surface
- **THEN** the sign is accepted there and the note is visible to other visitors
- **AND** the visitor cannot place the sign elsewhere in the managed hub

### Requirement: Keep player-submitted content inert
Player-submitted sign and book text MUST be treated as plain display text. It MUST NOT create click actions, execute commands, grant permissions, or alter controller state.

#### Scenario: Note contains command-like text
- **WHEN** a player submits text containing command syntax, selectors, JSON, or formatting-control characters
- **THEN** the hub displays it as inert text or rejects the unsafe characters
- **AND** no command or administrative action is executed

### Requirement: Provide feedback and announcement channels
The hub MUST provide a suggestion drop-off point and a separate administrator-managed notice area. Visitor suggestions MUST be distinguishable from announcements and MUST NOT replace administrator-controlled notices.

#### Scenario: Visitor submits a suggestion
- **WHEN** a visitor deposits a suggestion through the designated channel
- **THEN** the suggestion is retained for administrator review without becoming a public executable action

### Requirement: Support curated community displays
The hub SHOULD provide administrator-managed areas for welcome information, event notices, player showcases, and achievement or hall-of-fame displays. Curated content MUST use the same inert-text and managed-geometry protections as other hub displays.

#### Scenario: Administrator refreshes a showcase
- **WHEN** an administrator updates a curated display
- **THEN** the new display is visible in its assigned area
- **AND** destination bays, portal controls, and visitor protections remain unchanged
