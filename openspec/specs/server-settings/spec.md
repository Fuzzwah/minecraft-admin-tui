# Server Settings

## Purpose

Specify safe editing of an existing Minecraft server's `server.properties` file from the TUI.

## Requirements

### Requirement: Edit only supported properties already present
The TUI MUST offer edits for its supported server settings, including MOTD, player limit, difficulty, game modes, gameplay booleans, view and simulation distances, spawn protection, and idle kick timeout. It MUST NOT add absent properties or modify unknown properties. It MUST validate numeric ranges, booleans, and choices before writing.

#### Scenario: A supported setting is absent
- **WHEN** a supported setting is not present in `server.properties`
- **THEN** the TUI reports it as unavailable and MUST NOT add it with a guessed default

#### Scenario: A setting value is invalid
- **WHEN** a user submits a value outside the setting's accepted type, choices, or range
- **THEN** the TUI rejects the save without writing the file

### Requirement: Preserve unrelated file data
When saving supported changes, the TUI MUST preserve unknown properties, comments, credentials, file permissions, and numeric ownership. Before writing, it MUST reject an external change detected since the file was loaded. If file ownership cannot be determined safely, it MUST NOT write settings.

#### Scenario: The file changed outside the TUI
- **WHEN** `server.properties` differs from the loaded snapshot before the TUI writes
- **THEN** the save is rejected and the user is told to reload

#### Scenario: Podman ownership cannot be established
- **WHEN** the TUI cannot safely determine the properties file owner
- **THEN** it refuses the write and leaves settings unchanged

### Requirement: Respect container startup management
For itzg Minecraft images, settings saves and start/restart actions MUST be blocked unless `OVERRIDE_SERVER_PROPERTIES=false` or `SKIP_SERVER_PROPERTIES=true` is configured. The TUI MUST NOT recreate a container to apply those flags. Generic images are not gated, but their startup behavior must preserve file edits.

#### Scenario: Settings are startup-managed
- **WHEN** an itzg image has neither accepted environment flag
- **THEN** settings saves and start/restart are blocked with instructions to update the deployment and recreate the container while retaining its data volume

### Requirement: Saving does not apply settings live
Saving settings MUST write the existing file without applying values live or restarting the server. Failed saves MUST retain unsaved edits in the editor, and reload or close actions MUST confirm before discarding unsaved edits.

#### Scenario: Save edited settings
- **WHEN** the user saves valid changes
- **THEN** the file is updated and the server is not restarted automatically
