"""First-start setup that needs administrator rights: virtual display driver and firewall rules.

The program itself always runs as a normal user. Only these two things change Windows for every user, so
Windows reserves them to administrators:
- installing the Virtual Display Driver (a new device in the system);
- the firewall rules that let the devices reach port 8770 (private networks, iPhone hotspot, Tailscale).
On every start the server checks both without any special rights. If one is missing it asks once for
permission (the Windows "allow changes?" prompt) and runs tools\\setup.ps1, which does only the missing part.
Once both are in place the prompt never shows again. Saying no just means it asks again on the next start.
"""
import os
import subprocess

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SETUP = os.path.join(ROOT, 'tools', 'setup.ps1')
LOG = os.path.join(ROOT, 'data', 'setup.log')
RULE = 'Screen Extender (TCP 8770)'  # created by tools\firewall.ps1
NO_WINDOW = 0x08000000


def missing():
    """What still has to be set up: a list with 'driver' and/or 'firewall'. Needs no administrator rights."""
    out = []
    r = subprocess.run(['powershell', '-NoProfile', '-NonInteractive', '-Command',
                        "if (Get-PnpDevice -FriendlyName '*Virtual Display*' -ErrorAction SilentlyContinue)"
                        " { exit 0 } else { exit 3 }"],
                       capture_output=True, creationflags=NO_WINDOW, timeout=60)
    if r.returncode == 3:  # any other failure: unknown, better not to ask
        out.append('driver')
    r = subprocess.run(['netsh', 'advfirewall', 'firewall', 'show', 'rule', 'name=' + RULE],
                       capture_output=True, creationflags=NO_WINDOW, timeout=60)
    if r.returncode == 1:  # 1 = no rule with that name
        out.append('firewall')
    return out


def run():
    """Runs tools\\setup.ps1 as administrator and waits for it. Returns its exit code, None if permission was denied."""
    import pywintypes
    import win32event
    import win32process
    from win32com.shell import shell, shellcon
    os.makedirs(os.path.dirname(LOG), exist_ok=True)
    try:
        info = shell.ShellExecuteEx(
            fMask=shellcon.SEE_MASK_NOCLOSEPROCESS, lpVerb='runas', lpFile='powershell.exe',
            lpParameters=f'-NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File "{SETUP}" -Log "{LOG}"',
            nShow=0)
    except pywintypes.error as e:
        if e.winerror == 1223:  # the user answered no
            return None
        raise
    h = info['hProcess']
    try:
        win32event.WaitForSingleObject(h, 10 * 60 * 1000)
        return win32process.GetExitCodeProcess(h)
    finally:
        h.Close()
