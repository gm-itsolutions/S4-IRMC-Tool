#!/usr/bin/env python3
"""Eigene Lüfterkurve für Fujitsu PRIMERGY mit iRMC S4 (getestet: RX2520 M1).

Läuft auf dem Host, spricht die iRMC in-band über ipmitool an.
Undokumentierter Fujitsu-OEM-Befehl, empirisch ermittelt:
  0x2e 0xf5 80 28 00 2d 'F' 'W' 01 ff 80 <pct>  -> alle Lüfter auf <pct> % der Maximaldrehzahl
  0x2e 0xf5 80 28 00 2d 'F' 'W' 01 ff 00 00     -> zurück auf iRMC-Automatik

Sicherheit: Lesefehler, Grenztemperatur oder Beenden -> Automatik.

Aufruf:
  irmc-fan.py             Regelschleife (für systemd)
  irmc-fan.py --dry-run   nur anzeigen, was gesetzt würde
  irmc-fan.py --auto      sofort zurück auf Automatik
  irmc-fan.py --selftest  Kurvenlogik prüfen
"""
import re
import signal
import subprocess
import sys
import time

# --- Einstellungen ---------------------------------------------------------
INTERVAL = 10          # Sekunden zwischen zwei Messungen
MIN_PCT = 12           # Untergrenze: RAID-Controller (LSI 2108) hat keinen Sensor, braucht Luftstrom
MAX_STEP_DOWN = 2      # max. Absenkung pro Intervall in %-Punkten (gegen Pumpen)

# Kurven: (Temperatur °C, Lüfter %), dazwischen linear. Es gilt der höchste Wert aller Gruppen.
CURVES = {
    "cpu":     [(40, 10), (55, 15), (65, 25), (75, 45)],
    "mem":     [(45, 10), (60, 20), (70, 40)],
    "ambient": [(25, 10), (30, 20), (35, 40)],
    "disk":    [(38, 10), (45, 25), (50, 40)],
}
# Ab diesen Werten übernimmt die iRMC-Automatik.
LIMITS = {"cpu": 80, "mem": 80, "ambient": 35, "disk": 52}
# ---------------------------------------------------------------------------

OEM = ["ipmitool", "-I", "open", "raw", "0x2e", "0xf5", "0x80", "0x28", "0x00",
       "0x2d", "0x46", "0x57", "0x01", "0xff"]


def run(cmd):
    return subprocess.run(cmd, capture_output=True, text=True, timeout=20).stdout


def set_auto():
    subprocess.run(OEM + ["0x00", "0x00"], capture_output=True, timeout=20)


def set_pct(pct):
    r = subprocess.run(OEM + ["0x80", hex(pct)], capture_output=True, timeout=20)
    if r.returncode != 0:
        raise RuntimeError("Setzen fehlgeschlagen: " + r.stderr.decode().strip())


def read_ipmi():
    """CPU-, RAM- und Umgebungstemperaturen aus 'ipmitool sdr'."""
    temps = {"cpu": [], "mem": [], "ambient": []}
    for line in run(["ipmitool", "sdr", "type", "Temperature"]).splitlines():
        parts = [p.strip() for p in line.split("|")]
        if len(parts) < 5:
            continue
        m = re.match(r"(\d+(?:\.\d+)?) degrees C", parts[4])
        if not m:
            continue
        name, val = parts[0], float(m.group(1))
        if name.startswith("CPU"):
            temps["cpu"].append(val)
        elif name.startswith("MEM"):
            temps["mem"].append(val)
        elif name == "Ambient":
            temps["ambient"].append(val)
    return temps


def disk_devices():
    """Platten hinter dem MegaRAID, z. B. ['/dev/bus/0 -d megaraid,0', ...]."""
    try:
        out = run(["smartctl", "--scan"])
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return []
    return [l.split("#")[0].split() for l in out.splitlines() if "megaraid" in l]


def read_disks(devs):
    temps = []
    for dev in devs:
        out = run(["smartctl", "-A"] + dev)
        t = disk_temp(out)
        if t is not None:
            temps.append(t)
    return temps


def disk_temp(smart_out):
    m = re.search(r"Current Drive Temperature:\s+(\d+)", smart_out)  # SAS
    if m:
        return float(m.group(1))
    for line in smart_out.splitlines():  # SATA: Attribut 194, Rohwert = 10. Spalte
        f = line.split()
        if len(f) >= 10 and f[0] == "194":
            return float(f[9])
    return None


def interp(curve, t):
    if t <= curve[0][0]:
        return curve[0][1]
    for (t0, p0), (t1, p1) in zip(curve, curve[1:]):
        if t <= t1:
            return p0 + (p1 - p0) * (t - t0) / (t1 - t0)
    return curve[-1][1]


def target(temps):
    """None = Grenze überschritten oder CPU-Werte fehlen -> Automatik."""
    if not temps.get("cpu"):
        return None
    pct = MIN_PCT
    for group, vals in temps.items():
        if not vals:
            continue
        hot = max(vals)
        if hot >= LIMITS[group]:
            return None
        pct = max(pct, interp(CURVES[group], hot))
    return round(min(pct, 100))


def smooth(prev, new):
    if prev is None or new >= prev:
        return new
    return max(new, prev - MAX_STEP_DOWN)


def selftest():
    assert interp(CURVES["cpu"], 30) == 10
    assert interp(CURVES["cpu"], 60) == 20
    assert interp(CURVES["cpu"], 99) == 45
    assert target({"cpu": [35, 38], "mem": [33], "ambient": [21], "disk": []}) == MIN_PCT
    assert target({"cpu": [65], "mem": [], "ambient": [], "disk": []}) == 25
    assert target({"cpu": [40], "mem": [], "ambient": [], "disk": [46]}) == 28
    assert target({"cpu": [85], "mem": [], "ambient": [], "disk": []}) is None
    assert target({"cpu": [], "mem": [30], "ambient": [], "disk": []}) is None
    assert disk_temp("Current Drive Temperature:     31 C") == 31
    assert disk_temp("194 Temperature_Celsius 0x0022 070 050 000 Old_age Always - 30 (Min/Max 18/45)") == 30
    assert disk_temp("nichts") is None
    assert smooth(30, 12) == 28 and smooth(12, 30) == 30 and smooth(None, 12) == 12
    print("selftest ok")


def main():
    if "--selftest" in sys.argv:
        return selftest()
    if "--auto" in sys.argv:
        set_auto()
        return print("Automatik aktiv")
    dry = "--dry-run" in sys.argv

    def stop(*_):
        if not dry:
            set_auto()
        print("beendet, Automatik aktiv", flush=True)
        sys.exit(0)

    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)

    devs = disk_devices()
    print(f"Platten mit Temperatur: {len(devs)}", flush=True)
    current = None
    while True:
        temps = {}
        try:
            temps = read_ipmi()
            temps["disk"] = read_disks(devs)
            pct = target(temps)
        except Exception as e:  # Lesefehler -> sicherer Zustand
            print(f"Fehler beim Lesen: {e}", flush=True)
            pct = None

        hot = {g: max(v) for g, v in temps.items() if v} if pct is not None else {}
        if pct is None:
            if current != "auto":
                print(f"Automatik (Grenze/Fehler) {hot}", flush=True)
                if not dry:
                    set_auto()
            current = "auto"
        else:
            pct = smooth(current if isinstance(current, int) else None, pct)
            if pct != current:
                print(f"{pct} % {hot}", flush=True)
            if not dry:
                try:
                    set_pct(pct)  # jedes Intervall neu senden, falls die iRMC zurückstellt
                except Exception as e:
                    print(f"{e} -> Automatik", flush=True)
                    set_auto()
                    pct = "auto"
            current = pct
        time.sleep(INTERVAL)


if __name__ == "__main__":
    main()
