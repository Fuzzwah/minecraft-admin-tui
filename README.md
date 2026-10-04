# Minecraft Admin TUI

A Textual-based server cockpit for a Podman-hosted Minecraft Java server.

The app is designed around a **persistent selected player**: choose a player once in
the top context bar, then Give, Teleport, Player actions and Fun all operate on that
same player until you change the target.

Default container:

```text
mc_do_not_die
```

## What is implemented

### Players

- live active-player picker (`rcon-cli list`)
- selected-player context persists across tabs
- player health, food, XP, dimension and coordinates
- heal
- feed
- clear effects
- Survival / Creative / Spectator
- add 10 XP levels

### Give

- fuzzy item search / autocomplete
- aliases such as `fireworks`, `wither skull`, and `deep slate`
- quantity control
- exact Minecraft item IDs
- same generated-registry support as the original prototype

### Teleport

- selected player → another online player
- teleport to coordinates
- cross-dimension teleport
- save the selected player's current position as a named location
- teleport to / delete saved locations

Saved locations live at:

```text
~/.config/mc-admin-tui/locations.json
```

### World

- `keepInventory` on/off and status
- time: day / noon / night / midnight
- weather: clear / rain / thunder
- difficulty: peaceful / easy / normal / hard

### Server

- Podman container status
- `save-all`
- tail the last 100 container log lines
- raw RCON field
- restart the Minecraft container (requires typing `RESTART`)

### Activity

- in-app audit trail for admin actions

### Fun

- totem particles
- note-block ding
- glowing effect
- BONK title
- summon one chicken
- lightning strike

The lightning button is intentionally labelled as dangerous because it can hurt the
selected player.

## Install

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

python mc_admin_tui.py
```

Or install the project:

```bash
pip install .
mc-admin-tui
```

The old entry point is retained too:

```bash
python mc_give_tui.py
```

## Controls

- `Ctrl+R` — refresh online players
- `Ctrl+F` — focus item search
- `Ctrl+G` — give selected item
- `q` — quit when the focused widget isn't consuming the key

## Full item catalogue

The app ships with a starter catalogue so it works immediately.

For exact autocomplete against the Minecraft version you run, generate Mojang's
`registries.json` with the matching server jar:

```bash
java -DbundlerMainClass=net.minecraft.data.Main \
  -jar server.jar \
  --reports \
  --output generated
```

Then import it once:

```bash
python mc_admin_tui.py --registry generated/reports/registries.json
```

It caches item IDs at:

```text
~/.cache/mc-admin-tui/items.json
```

## RCON assumptions

The TUI uses the same pattern as the commands you've already been running:

```text
podman exec -i mc_do_not_die rcon-cli "<minecraft command>"
```

The Python process therefore needs permission to invoke Podman on the host.

## Notes

Player status is collected with vanilla `data get entity` commands. The TUI refreshes
the online-player list every 5 seconds and selected-player status every 10 seconds.

Backups are deliberately not included yet: a safe backup/restore UI should know the
actual world/volume paths and stop/save the server correctly before destructive
restore operations.
