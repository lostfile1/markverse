---
title: "CyberBot MK-I"
description: "Retro-futuristic droid avatar with dynamic mood expressions (/mood happy, angry, surprised, smirk)"
---

# Node: CyberBot
* **pos**: [0, 0, 0]
  ## Part: Torso
  * **pos**: [0, 0.95, 0]
  * **box**: { size: [0.68, 0.78, 0.42], color: "#1c2838", glow: "#061b29" }
  ## Part: ChestCore
  * **pos**: [0, 1.0, 0.23]
  * **sphere**: { radius: 0.13, color: "#00ffd9", glow: "#00ffd9" }
  ## Part: LeftLeg
  * **pos**: [-0.19, 0.32, 0]
  * **cylinder**: { radius: 0.11, height: 0.64, color: "#2c3e54", glow: "#081420" }
  ## Part: RightLeg
  * **pos**: [0.19, 0.32, 0]
  * **cylinder**: { radius: 0.11, height: 0.64, color: "#2c3e54", glow: "#081420" }
  ## Part: LeftArm
  * **pos**: [-0.47, 0.95, 0]
  * **cylinder**: { radius: 0.09, height: 0.66, color: "#2c3e54", glow: "#081420" }
  ## Part: RightArm
  * **pos**: [0.47, 0.95, 0]
  * **cylinder**: { radius: 0.09, height: 0.66, color: "#2c3e54", glow: "#081420" }
  ## Part: Head
  * **pos**: [0, 1.62, 0]
  * **box**: { size: [0.54, 0.46, 0.48], color: "#243447", glow: "#0a1c2e" }
  ## Part: Antenna
  * **pos**: [0, 1.98, 0]
  * **cone**: { radius: 0.07, height: 0.28, color: "#ff26bf", glow: "#ff26bf" }

  ## Part: VisorNeutral
  * **@expr**: [neutral, smirk]
  * **pos**: [0, 1.64, 0.25]
  * **box**: { size: [0.40, 0.11, 0.04], color: "#00ffd9", glow: "#00ffd9" }

  ## Part: VisorHappy
  * **@expr**: [happy]
  * **pos**: [0, 1.66, 0.25]
  * **box**: { size: [0.42, 0.14, 0.04], color: "#26ff75", glow: "#26ff75" }

  ## Part: VisorAngry
  * **@expr**: [angry]
  * **pos**: [0, 1.63, 0.25]
  * **box**: { size: [0.42, 0.09, 0.04], color: "#ff2942", glow: "#ff2942" }

  ## Part: VisorSurprised
  * **@expr**: [surprised]
  * **pos**: [0, 1.64, 0.25]
  * **sphere**: { radius: 0.12, color: "#ffcf33", glow: "#ffcf33" }
