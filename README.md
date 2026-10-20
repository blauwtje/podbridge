# podbridge

podbridge is a free Windows app. It makes AirPods Pro 3 your mic and your output in Discord at once, with stereo output and a wideband mic.

It is for the Asus TUF A15 FA507NV (Realtek RTL8852BE Bluetooth) with Secure Boot on. It uses no kernel driver and no test signing.

While podbridge runs, it owns the laptop's Bluetooth radio, so Windows Bluetooth is off. The radio goes back to Windows when podbridge exits.

## Quick start

1. Download `podbridge-setup.exe` from the GitHub Release.
2. Double-click it and accept the UAC prompt.
3. On "Windows protected your PC", click "More info", then "Run anyway".
4. Accept the one driver prompt (VB-CABLE).
5. Restart Windows.
6. Open "podbridge" from the Start Menu.
7. Hold the AirPods case button once.

podbridge checks your setup first. If something is wrong, it prints one line saying what to fix. Fix it and open "podbridge" again.

If all is well, you should see podbridge pair the AirPods and then print the devices to pick in Discord.

8. In Discord, set Input device to `CABLE Output`.
9. Set Output device to `Default`.

Done. Talk and listen.

## Install

1. Download `podbridge-setup.exe` from the GitHub Release.
2. Double-click it and accept the UAC prompt.
3. SmartScreen shows "Windows protected your PC". Click "More info", then "Run anyway".
4. Accept the one driver prompt (VB-CABLE).
5. Restart Windows.

Get `podbridge-setup.exe` from `github.com/blauwtje/podbridge/releases`. This is the only manual install. Later builds install themselves.

## First run and pairing

1. Click "podbridge" in the Start Menu.
2. Hold the AirPods case button once.

The first run pairs the AirPods. It saves the pairing in `%APPDATA%\podbridge`.

podbridge keeps its own pairing and does not use the Windows one. If the AirPods reject the saved pairing, podbridge drops it and the next run pairs again: hold the case button.

podbridge saves the pairing in the `%APPDATA%` of the account that approves the UAC prompt. If a different account approves it, podbridge pairs again and writes `doctor.log` in that account's folder.

To pair again, run:

```
podbridge pair
```

## Daily use

Click "podbridge" in the Start Menu. It is the only Start Menu item. Or run:

```
podbridge run
```

It prints the devices to pick in Discord:

- Input device: `CABLE Output`
- Output device: `Default`

While it runs, podbridge mutes the Windows default output (the laptop speakers). It captures that output and sends it to the AirPods.

To capture another device instead, use `--output-source <device>`. Then nothing is muted.

When you open podbridge and GitHub has a newer build, podbridge downloads it, installs it silently and starts again. To skip this check, set `PODBRIDGE_NO_UPDATE`. If a silent setup fails, nothing is shown. Read the setup log in your TEMP folder (`%TEMP%`).

Other commands:

- `podbridge selftest`
- `podbridge firmware` downloads Realtek firmware into `%APPDATA%\podbridge\firmware`. The installer already bundles it.

## The app window

`podbridge run` opens the podbridge window. It shows each run step, and any error comes with a next step. It also shows the AirPods Pro 3 battery and listening mode.

In the window you can change the settings the AirPods accept over AAP:

- Listening mode (Off, Noise Cancellation, Transparency, Adaptive)
- Press and hold actions, and the microphone choice
- Mute and End call presses
- Automatic ear detection, pause media when falling asleep, and charging case sounds
- Heart rate and Hearing Assist

If your AirPods do not accept a setting, the window says "Not supported on these AirPods".

Some features live only in Apple's software, so the window lists them greyed out: Firmware update, Find My, Hearing test and Ear tip fit test. Use an iPhone or Mac for those.

The window needs WebView2, which the installer adds. If the window cannot open, `podbridge run` falls back to the console. To use the console on purpose, run:

```
podbridge run --console
```

The install also has `podbridge-cli.exe`, a console build of the same app. Run it from a terminal to see the console output.

## Doctor

podbridge checks the admin rights, the Realtek USB ID, the firmware files, the VB-CABLE devices and the Windows speaker every time it starts. It stops at the first failure with one line saying what to fix.

For a full check of every stage, run this on the command line:

```
podbridge doctor
```

It checks each stage in order and stops at the first failure. The stages are:

1. Admin rights
2. Realtek USB ID
3. Firmware files
4. WinUSB bind
5. Bumble transport and firmware load
6. Bond
7. A2DP codec
8. AAP mic frames per second
9. VB-CABLE devices
10. Loopback audio while muted

It appends the results to `%APPDATA%\podbridge\doctor.log`.

## Restore and uninstall

podbridge gives the radio back to Windows when it exits, and again the next time it starts. If Bluetooth is still missing in Windows, open "podbridge" once, or run:

```
podbridge restore
```

To also delete the "libwdi autogenerated" certificates from the LocalMachine `Root` and `TrustedPublisher` stores, run:

```
podbridge restore --remove-cert
```

The uninstaller runs this for you.

## When something breaks

Bluetooth is gone in Windows after a crash: open "podbridge" again, or run `podbridge restore`.

podbridge prints one fix line and stops: do what it says, then open "podbridge" again.

You want every stage checked: run `podbridge doctor`, then read `%APPDATA%\podbridge\doctor.log`.

The AirPods need pairing again: run `podbridge pair`.

The speaker is still muted after a crash: run podbridge once and close it, or unmute it in Windows.

`podbridge restore` fails: use the manual fallback below.

### Manual fallback

1. Open an admin terminal.
2. List the drivers:

```
pnputil /enum-drivers
```

3. Find the libwdi WinUSB entry. Its published name is `oemNN.inf`.
4. Run:

```
pnputil /delete-driver oemNN.inf /uninstall /force
pnputil /scan-devices
```

Or restart Windows. Windows then reinstalls the Realtek driver.

## How it works

podbridge gives the Realtek driver back on exit. The installer already bundles the Realtek firmware.

## Run the newest source (developers)

To try a fix without a GitHub Actions build, run podbridge from source. It uses the installed app's firmware, `wdi-simple.exe`, driver state and window UI, so install `podbridge-setup.exe` once first. You need Git and uv (`winget install astral-sh.uv`).

Once:

```
git clone https://github.com/blauwtje/podbridge.git
```

Each time, in a normal (not admin) PowerShell:

```
powershell -ExecutionPolicy Bypass -File podbridge\scripts\dev.ps1
```

It fetches the current branch from the push remote, fast-forwards, runs `uv sync`, and starts `podbridge run` as admin in a new window (one UAC prompt). Pass other commands after it, such as `dev.ps1 doctor` or `dev.ps1 run --console`. Use `-Branch <name>` for another branch, `-NoPull` to keep the checkout, and `-InstallDir <folder>` when podbridge is not installed in the usual place.

Close the installed podbridge first; only one can own the radio. A source run never updates itself. It sets `PODBRIDGE_INSTALL_DIR` to the install folder. It uses the source UI only when you built it (`npm --prefix ui ci`, then `npm --prefix ui run build`).

## Notices

- Firmware: the installer bundles the unmodified Realtek firmware files from the Linux kernel firmware tree, with `LICENCE.rtlwifi_firmware.txt`, in `<install>\firmware`.
- VB-CABLE: the installer bundles the VB-CABLE driver pack from www.vb-cable.com. It is donationware by VB-Audio, so see their site to donate. It is installed only when no "CABLE Input" device exists.

## Licence

GPL-3.0. The AAP logic is ported from GPL-3.0 sources (ntpods, librepods, USBPods-Pico2W).
