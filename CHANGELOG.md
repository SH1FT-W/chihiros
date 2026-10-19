# Changelog

## 26.10.4 – 19.10.2026

- **Exact brightness:** channel values are sent unchanged, like the official app. Before, 90 % was sent as 89 % because the byte 0x5A was wrongly treated as reserved (only the message ID avoids it). Found in the protocol fixes of the original integration (v0.19.0).
- **Status messages on older lamps:** lamps with the HM-10 module now receive notifications on the same characteristic the app uses (FFE1). The previous characteristic is only used when FFE1 cannot notify, so nothing changes for lamps that already worked.

## 26.10.3 – 19.10.2026

- **All Chihiros LED lamps of the 0x5A family:** besides the C II RGB, the integration now supports A II, WRGB II
  (regular, Pro, Slim), WRGB VIVID III, Universal WRGB, SEA_LED, Tiny Terrarium Egg, Z Light TINY, Commander 1,
  Commander 4 and Commander X. The model is detected from the Bluetooth name; Home Assistant discovers all of them.
- The light has as many channels as the lamp (1 to 4): brightness for white and two-channel lamps, RGB for three
  colour channels, RGBW for true WRGB lamps (WRGB II Pro, VIVID III, Universal WRGB, SEA_LED, Commander 4).
- Channel sliders, `chihiros.set_channels`, `chihiros.set_auto_curve` and `chihiros.set_weekday_schedule` accept the
  channels of the lamp (`red`, `green`, `blue`, `white`, `warm`). Older lamps get their schedule points in
  30-minute steps like the app.
- Lite controllers (A II, Z Light TINY with a DYZSD name, Terrarium Egg, Commander 1) only accept a schedule for every day, and their
  schedule is written again after a power cut so that auto mode keeps running.
- Commander 1: the connected lamp (white, RGB or WRGB) is chosen during setup.
- WRGB VIVID III: fan entity (speed or automatic by temperature), fan speed and temperature sensors.
- The device page shows the detected model.
- C II RGB and dosing pump are unchanged (same entities, IDs and behaviour).
- Heaters and magnetic stirrers stay out on purpose.
- Prepared for public release: Apache 2.0 license and NOTICE (credits the original integration, MIT), English README
  with pictures in the project theme, project website (`docs/index.html`), issue template, HACS validation workflow,
  this changelog. Tests use fictitious Bluetooth addresses.

## 26.10.2 – 19.10.2026

- A late disconnect message no longer marks a working connection as broken; errors while sending a push message are
  caught.
- Tooling: GitHub checks, translation check and version tool from the shared template.

## 26.10.1 – 16.10.2026

- New version numbers: **year.month.number of the release in that month**. Home Assistant recognises `26.10.1` as
  newer than `1.0.12`. No other changes.

## 1.0.12 and earlier

- Rewrite of the integration with the same domain and the same unique IDs as TheMicDiet/chihiros-led-control for the
  C II RGB and the dosing pump: no blocking at Home Assistant start, one connection per device, dosing never
  repeated, daily schedules, weekday schedules, scenes, feeding light, sun cycle, acclimatization, dosing plans,
  calibration, container levels and push messages.
