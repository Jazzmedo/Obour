# Obour color palette

Obour means *crossing* in Arabic, the word painted on pedestrian crossings. The palette is a
road: dark asphalt, off-white road paint and one road-marking amber. No gradients.

| Name | Hex | Used for |
|---|---|---|
| Asphalt | `#1B1D20` | Background |
| Road paint | `#E8E4DA` | Zebra-crossing stripes, at 7% opacity on asphalt |
| Stripe on asphalt | `#292B2D` | What the stripes look like (road paint at 7% over asphalt) |
| Text | `#F1EEE7` | Name and feature labels |
| Muted text | `#B8B3A8` | Tagline |
| Amber | `#F2B134` | The only accent: the logo's sign, feature icons |

## Contrast

| Color | On asphalt `#1B1D20` | On a stripe `#292B2D` |
|---|---|---|
| Text `#F1EEE7` | 14.6:1 | 12.3:1 |
| Muted text `#B8B3A8` | 8.1:1 | 6.8:1 |
| Amber `#F2B134` | 8.9:1 | 7.5:1 |

All pass WCAG AA (4.5:1 for normal text).

## Type

**Poppins**: SemiBold for the name, Regular for the rest.

The banner (`assets/banner.svg`) is drawn by `assets/make-banner.py` with these values.
