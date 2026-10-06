# Local State

## Purpose

Specify local persistence of server registrations and user-created server-specific data.

## Requirements

### Requirement: Persist known servers and last selection
The TUI MUST persist registered server configurations and the last-used server in the user's configuration directory. Stored server credentials MUST be treated as sensitive local data.

#### Scenario: Reopen after registering a server
- **WHEN** the TUI starts after a server was registered and selected
- **THEN** its known-server entry and last-used server can be loaded from local configuration

### Requirement: Isolate saved locations by server
The TUI MUST store saved locations in files scoped to the server so identical names on different servers do not collide. If a legacy shared locations file exists and the first server-specific file does not, it MUST import the legacy data and rename the legacy file after import.

#### Scenario: Import legacy locations
- **WHEN** a server's location file does not exist and the legacy shared locations file is present
- **THEN** the legacy data is imported for that server and the legacy file is renamed as imported

### Requirement: Persist custom kits locally
The TUI MUST persist custom kits in the user's configuration directory and load them on later launches.

#### Scenario: Reopen after saving a custom kit
- **WHEN** the TUI starts after a custom kit has been saved
- **THEN** the custom kit is loaded from local configuration
