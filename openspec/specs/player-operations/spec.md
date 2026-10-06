# Player Operations

## Purpose

Specify player selection, status, inventory, administration, giving items, teleporting, and saved locations.

## Requirements

### Requirement: Maintain an active online-player target
The TUI MUST populate the player picker from the server's live online-player list. The selected player MUST remain the target across tabs while still online; when that player is no longer online, the TUI MUST select another online player or clear the target. Player actions MUST require an active selected player.

#### Scenario: Refresh online players
- **WHEN** the online-player list is refreshed and the current player remains online
- **THEN** the current player remains selected

#### Scenario: No players are online
- **WHEN** the refreshed online-player list is empty
- **THEN** the active target is cleared and player actions are unavailable

### Requirement: Inspect and administer the selected player
The TUI MUST display the selected player's health, food, experience level, dimension, coordinates, and inventory entries with item, quantity, and slot. It MUST provide heal, feed, clear-effects, game-mode, and experience actions for the selected player.

#### Scenario: Refresh selected-player status
- **WHEN** the user refreshes player status
- **THEN** the TUI queries the selected player's current status and inventory through RCON

### Requirement: Give an item to the selected player
The TUI MUST support item search and selection, exact item IDs, and a quantity from 1 through 6400. It MUST reject an invalid or missing selection or an out-of-range quantity before sending the RCON command.

#### Scenario: Give a selected item
- **WHEN** the user selects an item and enters a quantity from 1 to 6400
- **THEN** the TUI sends a give command for the active player

#### Scenario: Reject invalid quantity
- **WHEN** the requested quantity is not an integer from 1 through 6400
- **THEN** the TUI reports the error and sends no give command

### Requirement: Teleport players and save locations
The TUI MUST support teleporting the selected player to another online player, to numeric coordinates in the current or chosen dimension, and to a saved location. Saved locations MUST be stored per server and include coordinates and dimension; the user MUST be able to create and delete them.

#### Scenario: Save and revisit a location
- **WHEN** the user saves a named location from the selected player's current position
- **THEN** the TUI stores its coordinates and dimension for that server and can later teleport the selected player there
