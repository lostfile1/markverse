---
title: "Neon Courtyard"
description: "Starter #MV3D plaza demonstrating @def/@use templates, interactive triggers, signs, and portals"
tags: [starter, plaza, demo, neon]
sky: ["#050f1c", "#0a2938", "#0f3d47"]
fog: { color: "#08242e", range: 150 }
spawn: { pos: [0, 2.0, 12.0], yaw: 0 }
---

# Node: PlazaFloor
* **pos**: [0, -0.05, 0]
* **box**: { size: [64, 0.1, 64], color: "#0a121c", glow: "#030f17" }

# @def LampPost
* **cylinder**: { radius: 0.14, height: 3.6, color: "#1c2a3a", glow: "#071824" }
  ## Part: LanternOrb
  * **pos**: [0, 2.0, 0]
  * **sphere**: { radius: 0.34, color: "#00ffd9", glow: "#00b399" }

# Node: CourtyardLamps
  ## Part: Lamp_NW
  * **pos**: [-8.0, 1.8, -8.0]
  * **@use**: LampPost
  ## Part: Lamp_NE
  * **pos**: [8.0, 1.8, -8.0]
  * **@use**: LampPost
  ## Part: Lamp_SW
  * **pos**: [-8.0, 1.8, 6.0]
  * **@use**: LampPost
  ## Part: Lamp_SE
  * **pos**: [8.0, 1.8, 6.0]
  * **@use**: LampPost

# Node: CenterMonument
* **pos**: [0, 0, -4.0]
  ## Part: Pedestal
  * **pos**: [0, 0.4, 0]
  * **box**: { size: [4.5, 0.8, 4.5], color: "#142233", glow: "#062636" }
  ## Part: BeaconCrystal
  * **pos**: [0, 2.4, 0]
  * **cone**: { radius: 0.9, height: 2.8, color: "#00ffd9", glow: "#00cca8" }
  ## Part: WelcomeSign
  * **pos**: [0, 4.3, 0]
  * **sign**: { text: "WELCOME TO NEON COURTYARD", color: "#00ffd9", glow: "#006655" }

# Node: BeaconConsole
* **pos**: [3.6, 0.55, -2.5]
* **box**: { size: [0.7, 1.1, 0.7], color: "#ff26bf", glow: "#770055" }
* **trigger**: { action: "toggle_glow", target: "BeaconCrystal", mode: "toggle", interact: true, label: "Toggle Beacon Crystal" }

# Node: GateDoor
* **pos**: [0, 1.75, -14.0]
* **box**: { size: [4.2, 3.5, 0.4], color: "#203048", glow: "#0a2a40" }

# Node: GateSwitch
* **pos**: [-3.2, 0.55, -12.5]
* **box**: { size: [0.6, 1.1, 0.6], color: "#ffaa00", glow: "#664400" }
* **trigger**: { action: "move_offset", target: "GateDoor", offset: [0, 3.6, 0], mode: "toggle", interact: true, label: "Open / Close Gate" }

# Node: ReturnPortal
* **pos**: [-6.5, 1.2, -6.0]
* **portal**: { to: "hub", radius: 2.0, color: "#00ffd9" }
