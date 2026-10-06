# Server Management

## Purpose

Describe how the TUI discovers, selects, registers, and controls Minecraft server containers through the host's container runtime.

## Requirements

### Requirement: Discover Minecraft server containers
The TUI MUST scan each available supported runtime (Podman and Docker) and list containers whose image identifies an itzg Minecraft server, including stopped containers. A matching container MUST remain discoverable when it has no RCON client.

#### Scenario: Scan available runtimes
- **WHEN** the user scans for servers
- **THEN** the TUI probes available Podman and Docker runtimes and lists matching Minecraft containers

#### Scenario: Matching container has no RCON client
- **WHEN** a matching container does not contain a supported RCON client
- **THEN** it remains available for status and container lifecycle actions, while RCON actions report that no client is available

### Requirement: Detect RCON access
For discovered containers, the TUI MUST probe for `rcon-cli`, `mcrcon`, or `rcon`. When a client is present, it MUST resolve credentials from `server.properties` first, RCON client configuration second, and container environment variables last.

#### Scenario: Discover RCON credentials
- **WHEN** an RCON-enabled container is scanned
- **THEN** the TUI uses `rcon.password` and `rcon.port` from the server properties when present, falling back to client configuration and then `RCON_PASSWORD` and `RCON_PORT`

### Requirement: Register and remember servers
The TUI MUST allow a user to manually register a container not found by image-based discovery. It MUST remember registered server configurations and the last selected server, and preselect that server on a later launch when available.

#### Scenario: Select one running server
- **WHEN** a scan finds exactly one running server
- **THEN** the TUI selects it automatically

#### Scenario: Reopen the application
- **WHEN** the TUI starts after a server was selected previously
- **THEN** it preselects the remembered server

### Requirement: Control an existing container
The TUI MUST expose server status, logs, save-all and raw RCON operations, and start, stop, and restart actions for a registered container. Stop and restart MUST allow at least 60 seconds for graceful shutdown, or the container's configured stop timeout when greater. The TUI MUST operate on the existing container and MUST NOT recreate it or change its image, environment, ports, or volumes.

#### Scenario: Stop a server container
- **WHEN** the user confirms the stop action
- **THEN** the TUI requests a graceful container stop using the required timeout

#### Scenario: Start an itzg container with startup-managed properties
- **WHEN** `OVERRIDE_SERVER_PROPERTIES=false` and `SKIP_SERVER_PROPERTIES=true` are both absent
- **THEN** start and restart are blocked with guidance to change the deployment and recreate the container while retaining its data volume
