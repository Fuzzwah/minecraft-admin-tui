# World Operations

## Purpose

Specify live world controls and player-targeted fun actions available through RCON.

## Requirements

### Requirement: Control common world settings live
The TUI MUST provide live RCON controls for `keepInventory`, time of day, weather, and difficulty. It MUST resolve the supported `keepInventory` spelling against the selected server before reading or changing the gamerule.

#### Scenario: Server uses alternate gamerule spelling
- **WHEN** the server rejects one supported spelling of `keepInventory`
- **THEN** the TUI resolves and uses the spelling accepted by that server

#### Scenario: Change world time or weather
- **WHEN** the user chooses an available time, weather, or difficulty setting
- **THEN** the TUI sends the corresponding RCON command to the selected server

### Requirement: Provide selected-player effects and fun actions
The TUI MUST provide its player-targeted effects and actions, including totem particles, a note-block sound, glowing, a BONK title, chicken summon, and lightning strike. The lightning action MUST be identified as dangerous because it can harm the selected player.

#### Scenario: Use a player-targeted action
- **WHEN** the user triggers a player-targeted action
- **THEN** it targets the active selected player and appears in the in-app activity trail

### Requirement: Record administrative actions in the activity trail
The TUI MUST provide an in-app activity trail for administrative actions.

#### Scenario: Review recent administration
- **WHEN** administrative actions complete
- **THEN** the TUI records them in the in-app activity trail
