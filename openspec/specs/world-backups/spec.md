# World Backups

## Purpose

Specify creation and retention of compressed backups of Minecraft world directories.

## Requirements

### Requirement: Discover worlds and create an archive
The TUI MUST discover worlds from a container's data directory, including stopped containers where discovery can fall back to the configured level name and its `level.dat`. It MUST allow a world to be backed up for running or stopped containers by streaming the directory through the container runtime into a gzip-compressed tar archive. The backup MUST include the world directory and its nested dimensions. For a running server, the backup workflow MUST attempt `save-all` before streaming.

#### Scenario: Back up a running world
- **WHEN** the user starts a backup for a world on a running server
- **THEN** the TUI attempts `save-all` before streaming the world into an archive without stopping the server; if the command fails, the backup workflow continues and reports the skipped save

#### Scenario: Back up a stopped world
- **WHEN** the selected server is stopped and its world can be identified
- **THEN** the TUI creates the archive through the container copy stream without requiring container exec

### Requirement: Store and identify backups per server
The TUI MUST write backups under `~/minecraft-backups/<container>/` using a timestamped `.tar.gz` filename and MUST support an optional label. It MUST list the newest backups first.

#### Scenario: Create a labeled backup
- **WHEN** the user supplies a backup label
- **THEN** the label is included in the archive filename in that server's backup directory

### Requirement: Apply the chosen retention policy
The TUI MUST support retaining the newest 5, newest 10, or all backups, and MUST provide an explicit prune action. A retention value of all MUST retain every archive.

#### Scenario: Prune to a fixed retention count
- **WHEN** the user prunes with a retention count of 5 or 10
- **THEN** older archives are deleted and the newest requested number remain

### Requirement: Keep restore manual
The TUI MUST NOT restore backups automatically. It MUST provide manual restore instructions that tell the user to stop the container and extract the archive at the parent of the world directory, preserving the world directory name and removing a stale `session.lock` when present.

#### Scenario: Request restore guidance
- **WHEN** the user requests restore instructions for an archive
- **THEN** the TUI explains how to stop the container and manually extract the archive at the world directory's parent
