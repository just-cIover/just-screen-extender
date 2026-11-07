# Just Screen Extender

Use an **iPad or iPhone as an extra Windows monitor** over Wi-Fi, a USB cable or Tailscale.
Each device gets its own virtual screen (up to 4 at once), with touch, Apple Pencil pressure,
shared clipboard and file exchange.

The app is called **Screen Extender**, on the PC and on the devices. Its
interface is in **English or Italian**: pick it from the language menu with the flags (🇬🇧 GB / 🇮🇹 IT) in the PC window or in the app menu, and it applies to the PC and every
device. The full user guide is in Italian: [LEGGIMI.md](LEGGIMI.md).

## Features

- **Real extra screens:** a virtual display per device, remembered by Windows (position,
  resolution, scaling). It turns on when you open the app and off 15 s after you close it, and
  its windows move back to the main monitor.
- **Low-latency video:** FFmpeg captures the screen (Desktop Duplication) and encodes H.264 on the
  Intel GPU (Quick Sync), falling back to the CPU (x264). Automatic quality drops a level when the
  device or the network can't keep up.
- **Push to place:** like Universal Control on a Mac, push the mouse against the edge of the
  monitor on the side where the iPad is, and its screen moves there and the pointer goes in.
  With several devices connected, you tap *Here* on the right one.
- **Input:** touch works like a Windows touchscreen (or as a mouse). Apple Pencil is a real
  Windows Ink pen with pressure and tilt, and acts as a finger outside drawing apps. Two-finger
  zoom on the device only, and portrait mode.
- **Clipboard and files:** shared clipboard (text and images) both ways; a shared folder for
  files, the share sheet, and drag and drop from other iPad apps straight into Windows programs.
  Uploads show a progress bar with the amount sent, and say so if they stall.
- **Sleep aware:** when the PC sleeps (lid closed, standby) the program pauses video and its
  background checks, and the devices stop keeping their screen on. Everything resumes on wake.
- **Typing passwords:** fill a password from the iPad Keychain into the selected field on the PC,
  without going through the clipboard.
- **PC control window:** pairing PINs, console, CPU/GPU/disk temperatures (via
  LibreHardwareMonitor), device battery, quality controls and shared files.
- **Connections:** home Wi-Fi, iPhone hotspot (also over USB) and Tailscale, with automatic
  fallback between known addresses.

## Security

- HTTPS/WSS only (TLS 1.2+), with a local certificate authority created on the PC and limited
  to private and Tailscale addresses, so it cannot be used to impersonate other sites.
- Each new device is paired with a 6-digit PIN shown on the PC, and gets its own secret token
  (kept in the iOS Keychain; only its hash is stored on the PC). After repeated wrong PINs,
  pairing is locked for a while.
- The firewall rules (created on first start, `tools\firewall.ps1`) open the port only on private
  networks and to the local subnet, plus the iPhone hotspot and Tailscale ranges.
- The control window listens only on localhost. *Open on PC* opens documents and media only,
  never programs or scripts, and received files are marked as downloaded from the Internet.
- Clipboard items flagged as private by password managers are never sent, and the log never
  contains clipboard or password contents.

Everything personal (certificates, paired devices, settings, logs, shared files) lives in
`data\`, which is git-ignored.

## Requirements

- Windows 11 (developed on 11; Windows 10 may work) with an Intel GPU for Quick Sync. Without
  one it uses the CPU.
- Python 3.11+ and the packages in `requirements.txt`.
- [FFmpeg](https://ffmpeg.org/) on the PATH: `winget install Gyan.FFmpeg`.
- [Virtual Display Driver](https://github.com/VirtualDrivers/Virtual-Display-Driver): downloaded and
  installed by the program on its first start (through winget, which comes with Windows 11).
- Microsoft Edge, used for the PC control window.
- iPad or iPhone on iOS/iPadOS 17 or later: either the native app (sideloaded) or Safari.
- Optional: [LibreHardwareMonitor](https://github.com/LibreHardwareMonitor/LibreHardwareMonitor)
  for temperatures, [Tailscale](https://tailscale.com/) for use away from home.

## Setup

1. Install Python and FFmpeg, then open a terminal in the program folder and run:
   ```
   pip install -r requirements.txt
   ```
   This downloads the four Python packages the PC server needs (listed in `requirements.txt`):
   - `aiohttp`: the web server the devices connect to (pages, video and touch over WebSocket);
   - `cryptography`: creates the PC's own certificates for the encrypted HTTPS connection;
   - `Pillow`: images on the shared clipboard;
   - `pywin32`: access to Windows (Start menu shortcuts, drag and drop of shared files,
     the administrator prompt of step 2).

   pip only adds them to your Python installation: nothing else on the PC changes, and no
   administrator rights are needed.
2. Start the program with a double click on `avvio.pyw` (no console, opens the control window), or
   `Avvia.bat` (with a console). On its first start it sets everything up by itself:
   - it adds **Screen Extender** to the Start menu and to right-click → *Send to*: from then on
     just search for it in Start. The shortcuts are created only once (and fixed if you move the
     folder); changing language doesn't touch them;
   - it checks whether the virtual display driver and the firewall rules are in place. If one is
     missing, Windows asks *"Do you want to allow this app to make changes to your device?"*: answer
     **Yes**, and it installs the driver (with the resolutions in `driver\vdd_settings.xml`) and
     opens port 8770 on private networks only (see [Security](#security)). Progress and result show
     up in the console of the control window; details go to `data\setup.log`.

   **Why administrator.** The program itself always runs as a normal user. Only these two steps
   change Windows for every user, so Windows reserves them to administrators: installing a driver
   adds a new device (the virtual screens) to the system, and firewall rules decide which
   connections the whole PC accepts. They happen once, through `tools\setup.ps1`, which does only
   the missing part; once both are in place the prompt never shows again. If you answer *No*, the
   program still starts and asks again next time (without the driver there are no virtual screens,
   without the firewall rules the devices can't connect). The same steps can be run by hand:
   `driver\Installa driver.bat` and `tools\firewall.ps1`, both as administrator.
3. On the device:
   - **Native app (recommended):** download `ScreenExtender.ipa` from the
     [releases](../../releases) and sideload it with AltStore or SideStore. You can also build it
     yourself: the GitHub Actions workflow compiles an unsigned `.ipa`. Open the app, type the
     address shown in the PC window and enter the PIN.
   - **Safari:** open `https://<pc-address>:8770`, accept the warning the first time (or install
     the PC certificate from the menu), enter the PIN, then *Share → Add to Home Screen*.

Optional admin scripts in `tools\`: `sensori.ps1` (temperatures through LibreHardwareMonitor,
password-protected) and `usb-iphone.ps1` (iPhone over USB while the PC stays on Wi-Fi).

## Using it on several networks

Home, work, a phone hotspot, a friend's Wi-Fi: each PC creates **its own root certificate** on
first start. Install it once on each device (menu → File → *Secure connection*) and it keeps
working on every network:

- On a new network the PC notices the new address within 30 s and reissues its server
  certificate for it, signed by the same root. Devices trust it without reinstalling anything.
- Networks you have already used stay in the certificate (up to 16, dropping the ones unused
  the longest), so going back and forth between home and work changes nothing.
- The iPhone hotspot range (172.20.10.x, the same on every iPhone) is included from the start.
  The native app also looks for the PC there and on previously seen addresses when the usual
  one stops answering.
- Only private addresses (10.x, 172.16–31.x, 192.168.x) and Tailscale (100.64.0.0/10) are
  allowed: the root certificate is restricted to them by design.

The firewall rule opens the port only on **Private** networks, plus the iPhone hotspot and
Tailscale on any network. On a new home or work Wi-Fi, mark it as private in Windows
(*Settings → Network & internet → Wi-Fi → network → Private network*). Some work or public
networks isolate devices from each other: use the hotspot, a USB cable or Tailscale there.

## Project layout

```
avvio.pyw, Avvia.bat   start the server and the control window
pc\                    PC server (Python, aiohttp)
  web\                 web app for Safari
  panel\               PC control window
ios\                   native iPad/iPhone app (Swift, XcodeGen; built by GitHub Actions)
driver\                virtual display driver setup
tools\                 admin scripts: first-start setup, firewall, sensors, USB
data\                  your data, created at first start (never committed)
```

## Credits

- [Virtual Display Driver](https://github.com/VirtualDrivers/Virtual-Display-Driver) for the virtual screens
- [FFmpeg](https://ffmpeg.org/) for capture and encoding
- [aiohttp](https://github.com/aio-libs/aiohttp), [cryptography](https://github.com/pyca/cryptography),
  [Pillow](https://github.com/python-pillow/Pillow) and [pywin32](https://github.com/mhammond/pywin32)
- [LibreHardwareMonitor](https://github.com/LibreHardwareMonitor/LibreHardwareMonitor) for temperatures

## License

[MIT](LICENSE)
