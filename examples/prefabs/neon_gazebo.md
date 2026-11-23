---
title: "Prefab: neon_gazebo"
description: "Reusable 4-pillar neon gazebo blueprint for /stamp neon_gazebo"
tags: [prefab, blueprint, gazebo]
---

# @def neon_gazebo
  ## Part: FloorPad
  * **pos**: [0.000, 0.150, 0.000]
  * **box**: { size: [5.2, 0.3, 5.2], color: "#142233", glow: "#062436" }
  ## Part: Pillar_NW
  * **pos**: [-2.100, 1.800, -2.100]
  * **cylinder**: { radius: 0.22, height: 3.2, color: "#00ffd9", glow: "#009982" }
  ## Part: Pillar_NE
  * **pos**: [2.100, 1.800, -2.100]
  * **cylinder**: { radius: 0.22, height: 3.2, color: "#00ffd9", glow: "#009982" }
  ## Part: Pillar_SW
  * **pos**: [-2.100, 1.800, 2.100]
  * **cylinder**: { radius: 0.22, height: 3.2, color: "#00ffd9", glow: "#009982" }
  ## Part: Pillar_SE
  * **pos**: [2.100, 1.800, 2.100]
  * **cylinder**: { radius: 0.22, height: 3.2, color: "#00ffd9", glow: "#009982" }
  ## Part: CanopyRoof
  * **pos**: [0.000, 3.850, 0.000]
  * **cone**: { radius: 3.9, height: 1.5, color: "#ff26bf", glow: "#770055" }

# Node: PREFAB_PREVIEW
* **pos**: [0, 0, 0]
* **@use**: neon_gazebo
