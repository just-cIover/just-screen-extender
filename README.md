# Just Screen Extender

Use an **iPad or iPhone as an extra Windows monitor** over Wi-Fi, a USB cable or Tailscale.
Each device gets its own virtual screen (up to 4 at once), with touch, Apple Pencil pressure,
shared clipboard and file exchange.

The app itself is called **Schermo Esteso** and its interface is in Italian.
The full user guide is in Italian: [LEGGIMI.md](LEGGIMI.md).

## Features

- **Real extra screens:** a virtual display per device, remembered by Windows (position,
  resolution, scaling). It turns on when you open the app and off 15 s after you close it, and
  its windows move back to the main monitor.
- **Low-latency video:** FFmpeg captures the screen (Desktop Duplication) and encodes H.264 on the
  Intel GPU (Quick Sync), falling back to the CPU (x264). Automatic quality drops a level when the
  device or the network can't keep up.
- **Input:** touch works like a Windows touchscreen (or as a mouse). Apple Pencil is a real
  Windows Ink pen with pressure and tilt, and acts as a finger outside drawing apps. Two-finger
  zoom on the device only, and portrait mode.
- **Clipboard and files:** shared clipboard (text and images) both ways; a shared folder for
  files, the share sheet, and drag and drop from other iPad apps straight into Windows programs.
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
- The firewall script opens the port only on private networks and to the local subnet, plus the
  iPhone hotspot and Tailscale ranges.
- The control window listens only on localhost. *Open on PC* opens documents and media only,
  never programs or scripts, and received files are marked as downloaded from the Internet.
- Clipboard items flagged as private by password managers are never sent, and the log never
  contains clipboard or password contents.

Everything personal (certificates, paired devices, settings, logs, shared files) lives in
`dati\`, which is git-ignored.

## Requirements

- Windows 11 (developed on 11; Windows 10 may work) with an Intel GPU for Quick Sync. Without
  one it uses the CPU.
- Python 3.11+ and the packages in `requirements.txt`.
- [FFmpeg](https://ffmpeg.org/) on the PATH: `winget install Gyan.FFmpeg`.
- [Virtual Display Driver](https://github.com/VirtualDrivers/Virtual-Display-Driver):
  `winget install VirtualDrivers.Virtual-Display-Driver`.
- Microsoft Edge, used for the PC control window.
- iPad or iPhone on iOS/iPadOS 17 or later: either the native app (sideloaded) or Safari.
- Optional: [LibreHardwareMonitor](https://github.com/LibreHardwareMonitor/LibreHardwareMonitor)
  for temperatures, [Tailscale](https://tailscale.com/) for use away from home.

## Setup

1. Install the requirements above, then:
   ```
   pip install -r requirements.txt
   ```
2. Run `driver\Installa driver.bat` as administrator. It installs the virtual display driver
   with the resolutions in `driver\vdd_settings.xml`.
3. Run `strumenti\firewall.ps1` as administrator to open port 8770 on private networks only.
4. Start the program with `avvio.pyw` (no console, opens the control window), or
   `Avvia.bat` (with a console).
5. On the device:
   - **Native app (recommended):** download `SchermoEsteso.ipa` from the
     [releases](../../releases) and sideload it with AltStore or SideStore. You can also build it
     yourself: the GitHub Actions workflow compiles an unsigned `.ipa`. Open the app, type the
     address shown in the PC window and enter the PIN.
   - **Safari:** open `https://<pc-address>:8770`, accept the warning the first time (or install
     the PC certificate from the menu), enter the PIN, then *Share → Add to Home Screen*.

Optional admin scripts in `strumenti\`: `sensori.ps1` (temperatures through LibreHardwareMonitor,
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
programma\             PC server (Python, aiohttp)
  web\                 web app for Safari
  pannello\            PC control window
ios\                   native iPad/iPhone app (Swift, XcodeGen; built by GitHub Actions)
driver\                virtual display driver setup
strumenti\             admin scripts: firewall, sensors, USB
dati\                  your data, created at first start (never committed)
```

## Credits

- [Virtual Display Driver](https://github.com/VirtualDrivers/Virtual-Display-Driver) for the virtual screens
- [FFmpeg](https://ffmpeg.org/) for capture and encoding
- [aiohttp](https://github.com/aio-libs/aiohttp), [cryptography](https://github.com/pyca/cryptography),
  [Pillow](https://github.com/python-pillow/Pillow) and [pywin32](https://github.com/mhammond/pywin32)
- [LibreHardwareMonitor](https://github.com/LibreHardwareMonitor/LibreHardwareMonitor) for temperatures

## License

[MIT](LICENSE)
