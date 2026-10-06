# Item Catalogue and Kits

## Purpose

Specify item lookup and repeatable multi-item grants through the TUI.

## Requirements

### Requirement: Resolve the item catalogue
The TUI MUST resolve item IDs in this order: an explicitly selected registry file, the cached catalogue, the bundled registry, then the built-in fallback list. Item search MUST support fuzzy matching and exact item IDs.

#### Scenario: Explicit registry is supplied
- **WHEN** the user starts the TUI with a registry path
- **THEN** item lookup uses that registry ahead of cached and bundled catalogues

### Requirement: Provide built-in and custom kits
The TUI MUST provide built-in kits and allow users to create, persist, and delete custom kits. Custom kits MUST override a built-in kit with the same name, while built-in kits remain read-only. Item quantities in custom kits MUST be clamped to 1 through 6400.

#### Scenario: Custom kit overrides a built-in kit
- **WHEN** a custom kit uses the name of a built-in kit
- **THEN** the custom kit is used for that name and persists across launches

#### Scenario: Delete a built-in kit
- **WHEN** the user attempts to delete a built-in kit
- **THEN** the TUI refuses the deletion

### Requirement: Execute kit entries in order and report partial failure
A kit grant MUST send each entry in order for the active player. Entries representing item IDs MUST be sent as give commands; entries containing a space MUST be sent as bare commands. The TUI MUST report failed entries without hiding successful entries.

#### Scenario: A kit contains an invalid command
- **WHEN** one or more kit entries fail during execution
- **THEN** the TUI reports which entries succeeded and failed
