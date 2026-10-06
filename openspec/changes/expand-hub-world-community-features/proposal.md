# Proposal

## Why

The generated hub currently focuses on destination discovery, portal transfers, and administrator-controlled lifecycle actions. Visitors can browse destinations, but they have few safe reasons to stay in the hub, interact with one another, or return between server sessions. This change adds persistent social, recreational, exploratory, utility, cosmetic, and event-oriented hub experiences without weakening Adventure-mode protection or destination-control authorization.

## What Changes

- Add a protected community-message area with player notes, announcements, suggestions, welcome information, and curated showcases.
- Add a daily-present system and other repeatable, harmless hub activities with per-player cooldowns and bounded rewards.
- Add optional hub minigames and scoreboards, including parkour, target practice, races, mazes, and scavenger hunts.
- Add lore, hidden secrets, viewpoints, themed districts, and seasonal decoration points of interest.
- Add public utility spaces such as crafting, map, portal, seating, and approved shared-resource areas.
- Add cosmetic and collectible displays, including banners, heads, item exhibits, and hub-only collectibles.
- Add event infrastructure for announcements, ceremonies, build competitions, treasure hunts, fireworks, and community decorations.
- Preserve existing destination bays, owner-label signs, portal behavior, controller authorization, and managed-geometry boundaries.
- Keep all player-provided text and item data constrained to safe plain text or validated Minecraft data; player content MUST NOT create executable commands or grant administrative permissions.

## Capabilities

### New Capabilities

- `hub-community-messages`: Safe player notes, suggestions, announcements, welcome information, and curated community displays.
- `hub-rewards-and-activities`: Daily presents, repeatable challenges, cooldowns, eligibility, and bounded non-progression rewards.
- `hub-games-and-leaderboards`: Protected recreational courses and games with completion detection, personal records, and optional leaderboards.
- `hub-exploration-and-lore`: Hidden secrets, lore trails, viewpoints, themed areas, and seasonal points of interest.
- `hub-utility-spaces`: Public hub services and resource areas that remain protected from griefing and do not bypass destination-server progression.
- `hub-collectibles-and-events`: Cosmetic collections, display areas, and reusable event infrastructure.

### Modified Capabilities

- `world-operations`: Extend the generated hub world's managed geometry, datapack state, and safe Adventure-mode rules while preserving existing destination bays and controls.

## Impact

- Affects the vanilla hub generator, datapack generation, deployment/rebuild behavior, controller-facing configuration or state where needed, documentation, and hub-world tests.
- Does not change destination container lifecycle semantics or permit visitors to start, stop, or reconfigure destinations.
- Existing hub deployments require an explicit rebuild or equivalent migration before the new areas and datapack functions become available; ordinary controller restarts MUST NOT silently rewrite managed geometry.
- The initial design SHOULD make each feature independently disableable or safely absent so partially deployed hubs remain usable.
