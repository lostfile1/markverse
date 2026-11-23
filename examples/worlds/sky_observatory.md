---
title: "Sky Observatory"
description: "Elevated glass-and-neon observatory deck with custom geometric dome and viewing terrace"
tags: [observatory, space, architecture, example]
sky: ["#040714", "#101b3d", "#1d3566"]
fog: { color: "#081129", range: 180 }
spawn: { pos: [0, 2.2, 16.0], yaw: 0 }
---

# Node: MainDeck
* **pos**: [0, -0.1, 0]
* **box**: { size: [48, 0.2, 48], color: "#0e1526", glow: "#040a18" }

# Node: ObservationDais
* **pos**: [0, 0, -4.0]
  ## Part: LowerRing
  * **pos**: [0, 0.25, 0]
  * **cylinder**: { radius: 9.5, height: 0.5, color: "#18243d", glow: "#071c36" }
  ## Part: UpperRing
  * **pos**: [0, 0.75, 0]
  * **cylinder**: { radius: 6.5, height: 0.5, color: "#223457", glow: "#0d2b4d" }
  ## Part: TelescopePedestal
  * **pos**: [0, 1.8, 0]
  * **cylinder**: { radius: 0.85, height: 1.6, color: "#2b3f66", glow: "#006688" }
  ## Part: TelescopeTube
  * **pos**: [0, 3.1, -0.3]
  * **rot**: [-32, 0, 0]
  * **cylinder**: { radius: 0.55, height: 4.2, color: "#70a8ff", glow: "#1e50a8" }
  ## Part: LensGlow
  * **pos**: [0, 4.15, -1.4]
  * **sphere**: { radius: 0.48, color: "#00ffd9", glow: "#00ffd9" }

# Node: StarPrism
* **pos**: [0, 7.5, -4.0]
* **mesh**:
  * **points**: [[0, 1.4, 0], [1.1, 0, 0], [0, 0, 1.1], [-1.1, 0, 0], [0, 0, -1.1], [0, -1.4, 0]]
  * **faces**: [0, 1, 2, -1, 0, 2, 3, -1, 0, 3, 4, -1, 0, 4, 1, -1, 5, 2, 1, -1, 5, 3, 2, -1, 5, 4, 3, -1, 5, 1, 4, -1]
  * **color**: "#ff4dff"
  * **glow**: "#991f99"

# Node: DeckSign
* **pos**: [0, 3.8, 4.5]
* **sign**: { text: "CELESTIAL OBSERVATORY // DECK 01", color: "#70a8ff", glow: "#1b3b73" }

# Node: HubPortal
* **pos**: [0, 1.2, 10.5]
* **portal**: { to: "hub", radius: 2.0, color: "#70a8ff" }
