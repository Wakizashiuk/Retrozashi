# Eden first run: controllers + upscaling

A checklist for your first Retrozashi session with Eden. Say each 🗣️ line (Ctrl+Alt+Space to talk,
press again to stop) or type it in the Chat tab. **Ctrl+Alt+K stops everything instantly.**

Expect about 30–45 minutes. Most of that is step 2, which only happens once per Eden version.

---

## 0. Before you start (5 min)

- [ ] **Get the app.** Download `Retrozashi-win64` from the latest CI run's *Artifacts*, unzip it and run
      `Retrozashi\Retrozashi.exe`. Paste your Anthropic API key when asked (it's saved in Credential Manager).
      SmartScreen may warn about an unsigned app: *More info → Run anyway*.
- [ ] **Close Eden.**
- [ ] **Plug in or pair your controllers now**, before Eden starts. Eden only detects pads at launch.
- [ ] **Steam:** close it, or turn off Steam Input for your DualSense/DS4 and 8BitDo. Otherwise you get
      doubled or missing inputs.
- [ ] **8BitDo mode:** Switch mode (hold **Start + Y** while powering on) gives Nintendo button labels and
      motion on models with gyro. X-input mode (**Start + X**) looks like an Xbox pad. Pick one and keep it.
- [ ] **Don't run Eden as administrator.** Windows blocks simulated clicks into admin apps.
- [ ] In Retrozashi's right panel, pick the **monitor Eden opens on** in the dropdown.

## 1. Find Eden (1 min)

🗣️ *"Find Eden and tell me where its config file is."*

- ✅ You should see the path to `eden.exe` and to `qt-config.ini`, either in `user\config` next to the
  exe (portable) or in `%APPDATA%\eden\config`.
- ❌ If no config is found, open Eden once, close it, and ask again.
- ❌ If the exe isn't found, tell it where it is: *"Eden is in D:\Emulators\Eden"*.

## 2. Teach it your Eden build's graphics values (10–15 min, one time)

Eden stores options like "3x" as numbers that can differ between builds. Claude learns the real ones by
changing each option in Eden's own Graphics window and recording what Eden writes.

🗣️ *"Open Eden and learn the resolution options 1x, 1.5x, 2x and 3x from the Graphics settings, and
remember each one."*

🗣️ *"Also learn the upscale filter options for FSR and Bilinear, and the anti-aliasing options for
SMAA and None."*

- ✅ Eden opens, and Configure → Graphics changes on its own. The action log shows `snapshot_config`,
  `diff_config` and `remember_setting`.
- Hands off the mouse while it works.
- ✅ Check: 🗣️ *"What Eden settings have you learned?"* It should list them, e.g. *Resolution 3x =
  [Renderer] resolution_setup=5*.

## 3. Set the global upscaling (2 min)

🗣️ *"Set Eden's global resolution to 2x with the FSR filter."*
(2x docked = 4K. On a 1440p screen, 1.5x is a good match.)

- ✅ If Eden is open, **you get a Yes/No dialog** before it's closed. Say No and it uses Eden's own window
  instead.
- ✅ It finishes by reading the values back. 🗣️ *"Show me Eden's resolution settings"* to see for yourself.
- Want 4K-class output? Docked mode must be on: 🗣️ *"Make sure Eden is in docked mode."*

## 4. Per-game overrides (5 min)

🗣️ *"List my Switch games with their title IDs."*

🗣️ *"Set Tears of the Kingdom to 1x and Mario Kart 8 to 3x, as per-game overrides."*

- ✅ Each game gets its own `config\custom\<TitleID>.ini`, and your global 2x stays as it is.
- If a file name doesn't contain the title ID, Claude reads it from Eden (right-click the game →
  Properties).
- Back to global for a game: 🗣️ *"Make Mario Kart use the global resolution again."*

## 5. Controllers (5 min each)

🗣️ *"Set up my DualSense as Player 1 in Eden as a Pro Controller with motion, and save it as an
input profile called DualSense."*

- ✅ Eden opens Configure → Controls → Player 1. The Input Device becomes your DualSense, Eden
  auto-maps it, the type becomes Pro Controller and Motion 1 is mapped. Then it clicks **OK**.
- ✅ The action log shows `diff_config` with `[Controls] player_0_*` keys changing.

🗣️ *"Now do the same for my 8BitDo and save it as a profile called 8BitDo."*

Switch between pads later: 🗣️ *"Load the 8BitDo input profile for Player 1 in Eden."*

## 6. Test in a game (5 min)

- [ ] Launch a game in Eden and check that A/B/X/Y are in the expected places, that both sticks work,
      and that motion works (DualSense, or 8BitDo in Switch mode).
- [ ] Check the resolution looks right (Eden's status bar shows the scale).
- [ ] 🗣️ *"In Eden, what is Player 1's A button mapped to?"* confirms it reads controller config.

## Undo and safety

- **↶ Undo** (right panel) reverts every config file the last request changed, without an API call.
- 🗣️ *"Undo the last Eden change"* or *"Show me Eden's backups"* restores from timestamped backups.
- **Ctrl+Alt+K** / **HALT** stops all mouse and keyboard control immediately.

## Troubleshooting

| Symptom | Fix |
|---|---|
| Clicks do nothing in Eden | Eden is running as admin; run it normally. |
| Controller missing from Input Device | Connect it before starting Eden; close Steam; on 8BitDo, re-enter the mode. |
| Doubled or ghost inputs | Steam Input is still on for that pad. |
| A/B swapped | Expected with 8BitDo in X-input mode or Nintendo layout. Ask: *"swap A and B for Player 1 in Eden"*. |
| "diff shows no changes" | The option was cancelled instead of OK'd, or this setting only saves when Eden exits. Ask it to retry. |
| Anything else | `%APPDATA%\Retrozashi\retrozashi.log` has details. |

## Send these back afterwards 🙏

Paste or attach **`%APPDATA%\Retrozashi\learned_settings.json`**. It holds the real values for your
Eden build, so I can make them the built-in defaults and replace the yuzu-era guesses.
