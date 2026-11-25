---
title: "Neo-Arcade Showcase Gallery"
description: "Sample #MV3D world with an interactive gate switch, embedded media screen, and return portal"
tags: [showcase, demo, makerverse]
sky: ["#060414", "#1a0b3b"]
fog: { color: "#120829", range: 160 }
spawn: { pos: [0, 2.0, 12.0], yaw: 0 }
---

# Node: Ground
* **pos**: [0, -0.05, 0]
* **box**: { size: [120, 0.1, 120], color: "#0b1021", glow: "#040612" }

# @def NeonBeacon
* **cylinder**: { radius: 0.35, height: 3.6, color: "#00ffd9", glow: "#00a896" }
 ## Part: BeaconOrb
 * **pos**: [0, 2.1, 0]
 * **sphere**: { radius: 0.45, color: "#ff2a9d", glow: "#ff2a9d" }

# Node: LeftBeacon
* **pos**: [-5.0, 1.8, -2.0]
* **@use**: NeonBeacon

# Node: RightBeacon
* **pos**: [5.0, 1.8, -2.0]
* **@use**: NeonBeacon

# Node: VaultDoor
* **pos**: [0, 1.6, -8.0]
* **box**: { size: [3.6, 3.2, 0.4], color: "#00ffd9", glow: "#004433" }

# Node: VaultSwitch
* **pos**: [-2.6, 1.0, -6.0]
* **trigger**: { action: "move_offset", target: "VaultDoor", offset: [0, 3.4, 0], radius: 3.0, mode: "toggle", interact: true, label: "Toggle Vault Door" }

# Node: HubReturnPortal
* **pos**: [7.0, 1.2, -6.0]
* **portal**: { to: "hub", radius: 2.0, color: "#ff26d1" }
